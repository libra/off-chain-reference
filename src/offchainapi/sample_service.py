from .business import BusinessContext, BusinessAsyncInterupt, BusinessForceAbort, \
BusinessValidationFailure, VASPInfo
from .protocol import OffChainVASP
from .libra_address import LibraAddress
from .protocol_messages import CommandRequestObject
from .payment_logic import PaymentCommand, PaymentProcessor
from .status_logic import Status
from .storage import StorableFactory
from .auth_networking import AuthNetworkServer, NetworkFactory

import json
import OpenSSL.crypto

business_config = """[
    {
        "account": "1",
        "stable_id": "A",
        "balance": 10.0,
        "entity": false,
        "kyc_data" : "{ 'name' : 'Alice' }",
        "pending_transactions" : {}
    },
    {
        "account": "2",
        "stable_id": "B",
        "balance": 100.0,
        "entity": true,
        "kyc_data" : "{ 'name' : 'Bob' }",
        "pending_transactions" : {}
    }
]"""


class sample_vasp_info(VASPInfo):
    def __init__(self, assets_path):

        # assets_path = '../test_vectors/'

        tls_cert = assets_path / 'server_cert.pem'
        tls_key = assets_path / 'server_key.pem'
        all_peers_tls_cert = assets_path / 'client_cert.pem'

        peerA_addr = LibraAddress.encode_to_Libra_address(b'A'*16).as_str()
        each_peer_tls_cert = {
            peerA_addr: str(assets_path / 'client_cert.pem'),
        }
        each_peer_base_url = {
            peerA_addr: 'https://peerA.com',
        }

        self.tls_cert = str(tls_cert)
        self.tls_key = str(tls_key)
        self.all_peers_tls_cert = str(all_peers_tls_cert)
        self.each_peer_tls_cert = each_peer_tls_cert
        self.each_peer_base_url = each_peer_base_url

    def get_TLS_certificate_path(self):
        return self.tls_cert

    def get_TLS_key_path(self):
        return self.tls_key

    def get_peer_TLS_certificate_path(self, other_addr):
        assert other_addr.as_str() in self.each_peer_tls_cert
        return self.each_peer_tls_cert[other_addr.as_str()]

    def get_all_peers_TLS_certificate_path(self):
        return self.all_peers_tls_cert

    def get_peer_base_url(self, other_addr):
        assert other_addr.as_str() in self.each_peer_base_url
        return self.each_peer_base_url[other_addr.as_str()]

    def is_authorised_VASP(self, certificate, other_addr):
        # The check below should always pass: if we managed to open a channel
        # with another VASP, we should have already loaded its certificate.
        assert other_addr.as_str() in self.each_peer_tls_cert

        # Check that the certificate provided with the request matches the
        # certificate we have in store for the given address.
        cert_file = self.each_peer_tls_cert[other_addr.as_str()]
        with open(cert_file, 'rt') as f:
            cert_str = f.read()
        local_cert = OpenSSL.crypto.load_certificate(
            OpenSSL.crypto.FILETYPE_PEM, cert_str
        )
        return local_cert.get_serial_number() == certificate.get_serial_number()


class sample_business(BusinessContext):
    def __init__(self, my_addr):
        self.my_addr = my_addr
        self.accounts_db = json.loads(business_config)

    # Helper functions for the business

    def get_address(self):
        return self.my_addr.encoded_address

    def get_account(self, subaddress):
        for acc in self.accounts_db:
            if acc['account'] ==  subaddress:
                return acc
        raise BusinessValidationFailure('Account %s does not exist' % subaddress)

    def assert_payment_for_vasp(self, payment):
        sender = payment.sender
        receiver = payment.receiver

        if sender.address == self.get_address() or \
            receiver.address == self.get_address():
            return
        raise BusinessValidationFailure()

    def has_sig(self, payment):
            # Checks if the payment has the signature necessary
            return 'recipient_signature' in payment.data

    # Implement the business logic interface

    def open_channel_to(self, other_vasp_info):
        return

    def close_channel_to(self, other_vasp_info):
        return

    def check_account_existence(self, payment):
        self.assert_payment_for_vasp(payment)
        accounts = {acc['account'] for acc in self.accounts_db}
        if self.is_sender(payment):
            if payment.sender.subaddress in accounts:
                return
        else:
            if payment.receiver.subaddress in accounts:
                return
        raise BusinessForceAbort('Subaccount does not exist.')

    def is_sender(self, payment):
        self.assert_payment_for_vasp(payment)
        return payment.sender.address == self.get_address()
    

    def validate_recipient_signature(self, payment):
        if 'recipient_signature' in payment.data:
            if payment.recipient_signature == 'VALID':
                return
            raise BusinessValidationFailure('Invalid signature: %s' % payment.data.get('recipient_signature', 'Not present'))

    def get_recipient_signature(self, payment):
        return 'VALID'

    def get_my_role(self, payment):
        my_role = ['receiver', 'sender'][self.is_sender(payment)]
        return my_role

    def get_other_role(self, payment):
        other_role = ['sender', 'receiver'][self.is_sender(payment)]
        return other_role

    def next_kyc_to_provide(self, payment):
        my_role = self.get_my_role(payment)
        other_role = self.get_other_role(payment)

        subaddress = payment.data[my_role].subaddress
        account = self.get_account(subaddress)

        if account['entity']:
            return { Status.needs_kyc_data }

        to_provide = set()
        if payment.data[other_role].status == Status.needs_stable_id:
                to_provide.add(Status.needs_stable_id) 
        
        if payment.data[other_role].status == Status.needs_kyc_data:
                to_provide.add(Status.needs_stable_id) 
                to_provide.add(Status.needs_kyc_data) 
        
        if payment.data[other_role].status == Status.needs_recipient_signature:
                if my_role == 'receiver':
                    to_provide.add(Status.needs_recipient_signature)

        return to_provide


    def next_kyc_level_to_request(self, payment):
        my_role = self.get_my_role(payment)
        other_role = self.get_other_role(payment)
        subaddress = payment.data[my_role].subaddress
        account = self.get_account(subaddress)

        if account['entity']:
            # Put the money aside for this payment ...
            return Status.none

        if 'kyc_data' not in payment.data[other_role].data:
            return Status.needs_kyc_data

        if 'recipient_signature' not in payment.data and my_role == 'sender':
            return Status.needs_recipient_signature

        return payment.data[my_role].status


    def validate_kyc_signature(self, payment):
        other_role = ['sender', 'receiver'][self.is_sender(payment)]
        if 'kyc_signature' in payment.data[other_role].data:
            if not payment.data[other_role].kyc_signature == 'KYC_SIG':
                raise BusinessValidationFailure()

    def get_extended_kyc(self, payment):
        ''' Gets the extended KYC information for this payment.

            Can raise:
                   BusinessAsyncInterupt
                   BusinessNotAuthorized.
        '''
        my_role = self.get_my_role(payment)
        subaddress = payment.data[my_role].subaddress
        account = self.get_account(subaddress)
        return (account["kyc_data"], 'KYC_SIG', 'KYC_CERT')


    def get_stable_id(self, payment):
        my_role = self.get_my_role(payment)
        subaddress = payment.data[my_role].subaddress
        account = self.get_account(subaddress)
        return account["stable_id"]


    def ready_for_settlement(self, payment):
        my_role = self.get_my_role(payment)
        other_role = self.get_other_role(payment)
        subaddress = payment.data[my_role].subaddress
        account = self.get_account(subaddress)

        if my_role == 'sender': 
            if account["balance"] >= payment.action.amount:

                # Reserve the amount for this payment
                reference = payment.reference_id
                if reference not in account['pending_transactions']:
                    account['pending_transactions'][reference] = {
                        "amount": payment.action.amount
                    }
                    account["balance"] -= payment.action.amount

            else:
                if reference not in account['pending_transactions']:
                    raise BusinessForceAbort('Insufficient Balance')

        # This VASP always settles payments on chain, so we always need
        # a signature to settle on chain.
        if not self.has_sig(payment):
            return False

        # This VASP subaccount is a business
        if account['entity']:
            return True

        # The other VASP subaccount is a business
        if 'kyc_data' in payment.data[other_role].data and \
            payment.data[other_role].data['kyc_data'].parse()['type'] == 'entity':
            return True

        # Simple VASP, always requires kyc data for individuals
        if 'kyc_data' in payment.data[other_role].data and 'kyc_data' in payment.data[my_role].data:
            return True

        # We are not ready to settle yet!
        return False

    def want_single_payment_settlement(self, payment):
        return True

    def has_settled(self, payment):
        if payment.sender.status == Status.settled:
            # In this VASP we consider we are ready to settle when the sender
            # says so (in reality we would check on-chain as well.)
            my_role = self.get_my_role(payment)
            subaddress = payment.data[my_role].subaddress

            account = self.get_account(subaddress)
            reference = payment.reference_id

            if reference not in account['pending_transactions']:
                account['pending_transactions'][reference] = { 'settled':False }

            if not account['pending_transactions'][reference]['settled']:
                account["balance"] += payment.action.amount
                account['pending_transactions'][reference]['settled'] = True

            return True
        else:
            raise BusinessAsyncInterupt(1234)


class sample_vasp:

    def __init__(self, my_addr, assets_path):
        self.my_addr = my_addr
        self.bc = sample_business(self.my_addr)
        self.store        = StorableFactory({})
        self.info_context = sample_vasp_info(assets_path)
        self.network_factory = NetworkFactory()

        self.pp = PaymentProcessor(self.bc, self.store)
        self.vasp = OffChainVASP(
            self.my_addr, self.pp, self.store, self.info_context, self.network_factory
        )

        # The network server
        self.network_server = AuthNetworkServer(
            self.vasp,
            self.info_context.get_TLS_key_path(),
            self.info_context.get_TLS_certificate_path(),
            self.info_context.get_all_peers_TLS_certificate_path()
        )

    def collect_messages(self):
        messages = []
        for channel in self.vasp.channel_store.values():
            messages += channel.net_queue
            del channel.net_queue[:]
        return messages

    def get_channel(self, other_vasp):
        channel = self.vasp.get_channel(other_vasp)
        return channel

    def process_request(self, other_vasp, request_json):
        # Get the channel
        channel = self.get_channel(other_vasp)
        channel.parse_handle_request(request_json)

    def insert_local_command(self, other_vasp, command):
        channel = self.get_channel(other_vasp)

        if command.depend_on != []:
            assert len(channel.executor.object_store) > 0
        channel.sequence_command_local(command)

    def process_response(self, other_vasp, request_json):
        channel = self.get_channel(other_vasp)
        channel.parse_handle_response(request_json)

    def run_server(self):
        self.network_server.run()