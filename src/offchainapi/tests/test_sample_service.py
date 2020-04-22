from ..sample_service import sample_business, sample_vasp
from ..payment_logic import Status, PaymentProcessor, PaymentCommand
from ..payment import PaymentActor, PaymentObject
from ..libra_address import LibraAddress
from ..utils import JSONFlag
from ..protocol_messages import CommandRequestObject, CommandResponseObject, \
    OffChainError

from pathlib import Path
import OpenSSL.crypto
import json
from unittest.mock import MagicMock, patch
import pytest
import asyncio


@pytest.fixture
def business_and_processor(three_addresses, store):
    _, _, a0 = three_addresses
    bc = sample_business(a0)
    proc = PaymentProcessor(bc, store)
    proc.loop = asyncio.new_event_loop()
    return (bc, proc)


@pytest.fixture
def payment_as_receiver(three_addresses, sender_actor, payment_action):
    _, _, a0 = three_addresses
    receiver = PaymentActor(a0.as_str(), '1', Status.none, [])
    return PaymentObject(
        sender_actor, receiver, 'ref', 'orig_ref', 'desc', payment_action
    )


@pytest.fixture
def kyc_payment_as_receiver(payment_as_receiver, kyc_data):
    payment = payment_as_receiver
    payment.sender.add_kyc_data(kyc_data, 'KYC_SIG', 'CERT')
    payment.receiver.add_kyc_data(kyc_data, 'KYC_SIG', 'CERT')
    payment.sender.change_status(Status.needs_recipient_signature)
    return payment


@pytest.fixture
def settled_payment_as_receiver(kyc_payment_as_receiver):
    payment = kyc_payment_as_receiver
    payment.add_recipient_signature('SIG')
    payment.sender.change_status(Status.settled)
    return payment


@pytest.fixture
def payment_as_sender(three_addresses, receiver_actor, payment_action):
    _, _, a0 = three_addresses
    sender = PaymentActor(a0.as_str(), '1', Status.none, [])
    return PaymentObject(
        sender, receiver_actor, 'ref', 'orig_ref', 'desc', payment_action
    )


@pytest.fixture
def kyc_payment_as_sender(payment_as_sender, kyc_data):
    payment = payment_as_sender
    payment.sender.add_kyc_data(kyc_data, 'KYC_SIG', 'CERT')
    payment.receiver.add_kyc_data(kyc_data, 'KYC_SIG', 'CERT')
    payment.sender.change_status(Status.needs_recipient_signature)
    payment.add_recipient_signature('SIG')
    assert payment.sender is not None
    return payment


@pytest.fixture
def my_addr(three_addresses):
    _, _, a0 = three_addresses
    return a0


@pytest.fixture
def other_addr(three_addresses):
    a0, _, _ = three_addresses
    return a0


@pytest.fixture
def asset_path(request):
    asset_path = Path(request.fspath).resolve()
    asset_path = asset_path.parents[3] / 'test_vectors'
    return asset_path


@pytest.fixture
def vasp(my_addr, asset_path):
    return sample_vasp(my_addr, asset_path)


@pytest.fixture(params=[
    (None, None, 'failure', True, 'parsing'),
    (0, 0, 'success', None, None),
    (0, 0, 'success', None, None),
    (10, 10, 'success', None, None),
])
def simple_response_json_error(request):
    seq, cmd_seq, status, protoerr, errcode = request.param
    resp = CommandResponseObject()
    resp.status = status
    resp.seq = seq
    resp.command_seq = cmd_seq
    if status == 'failure':
        resp.error = OffChainError(protoerr, errcode)
    json_obj = resp.get_json_data_dict(JSONFlag.NET)
    return json_obj


@pytest.fixture
def simple_request_json(payment_action, my_addr, other_addr):
    sender = PaymentActor(other_addr.as_str(), 'C', Status.none, [])
    receiver = PaymentActor(my_addr.as_str(), '1', Status.none, [])
    payment = PaymentObject(
        sender, receiver, 'ref', 'orig_ref', 'desc', payment_action
    )
    command = PaymentCommand(payment)
    request = CommandRequestObject(command)
    request.seq = 0
    return request.get_json_data_dict(JSONFlag.NET)


def test_business_simple(my_addr):
    bc = sample_business(my_addr)


def test_business_is_related(business_and_processor, payment_as_receiver):
    bc, proc = business_and_processor
    payment = payment_as_receiver

    kyc_level = proc.loop.run_until_complete(bc.next_kyc_level_to_request(payment))
    assert kyc_level == Status.needs_kyc_data

    ret_payment = proc.payment_process(payment)
    assert ret_payment.has_changed()
    assert ret_payment.receiver.status == Status.needs_kyc_data


def test_business_is_kyc_provided(business_and_processor, kyc_payment_as_receiver):
    bc, proc = business_and_processor
    payment = kyc_payment_as_receiver

    kyc_level = proc.loop.run_until_complete(bc.next_kyc_level_to_request(payment))
    assert kyc_level == Status.none

    ret_payment = proc.payment_process(payment)
    assert ret_payment.has_changed()

    ready = proc.loop.run_until_complete(bc.ready_for_settlement(ret_payment))
    assert ready
    assert ret_payment.receiver.status == Status.ready_for_settlement

def test_business_is_kyc_provided_sender(business_and_processor, kyc_payment_as_sender):
    bc, proc = business_and_processor
    payment = kyc_payment_as_sender
    assert bc.is_sender(payment)
    kyc_level = proc.loop.run_until_complete(bc.next_kyc_level_to_request(payment))
    assert kyc_level == Status.needs_recipient_signature

    ret_payment = proc.payment_process(payment)
    assert ret_payment.has_changed()

    ready = proc.loop.run_until_complete(bc.ready_for_settlement(ret_payment))
    assert ready
    assert ret_payment.data['sender'].data['status'] == Status.ready_for_settlement
    assert bc.get_account('1')['balance'] == 5.0


def test_business_settled(business_and_processor, settled_payment_as_receiver):
    bc, proc = business_and_processor
    payment = settled_payment_as_receiver

    ret_payment = proc.payment_process(payment)
    assert ret_payment.has_changed()

    ready = proc.loop.run_until_complete(bc.ready_for_settlement(ret_payment))
    assert ready
    assert ret_payment.data['receiver'].status == Status.settled

    assert bc.get_account('1')['pending_transactions']['ref']['settled']
    assert bc.get_account('1')['balance'] == 15.0


@pytest.fixture(params=[
    (None, None, 'failure', True, 'parsing'),
    (0, 0, 'success', None, None),
    (0, 0, 'success', None, None),
    (10, 10, 'success', None, None),
    ])
def simple_response_json_error(request):
    seq, cmd_seq, status, protoerr, errcode =  request.param
    sender_addr = LibraAddress.encode_to_Libra_address(b'A'*16).encoded_address
    receiver_addr   = LibraAddress.encode_to_Libra_address(b'B'*16).encoded_address
    resp = CommandResponseObject()
    resp.status = status
    resp.seq = seq
    resp.command_seq = cmd_seq
    if status == 'failure':
        resp.error = OffChainError(protoerr, errcode)
    json_obj = json.dumps(resp.get_json_data_dict(JSONFlag.NET))
    return json_obj

def test_vasp_simple(simple_request_json, vasp, other_addr):
    vasp.pp.start_processor()

    try:
        vasp.process_request(other_addr, simple_request_json)
        requests = vasp.collect_messages()
        assert len(requests) == 1
        assert requests[0].type is CommandResponseObject
        
        for fut in vasp.pp.futs:
            fut.result()
        requests = vasp.collect_messages()
        assert len(requests) == 1
        assert requests[0].type is CommandRequestObject
    finally:
        vasp.pp.stop_processor()


def test_vasp_simple_wrong_VASP(simple_request_json, asset_path, other_addr):
    my_addr = LibraAddress.encode_to_Libra_address(b'X'*16)
    vasp = sample_vasp(my_addr, asset_path)

    try:
        vasp.pp.start_processor()
        vasp.process_request(other_addr, simple_request_json)
        responses = vasp.collect_messages()
        assert len(responses) == 1
        assert responses[0].type is CommandResponseObject
        assert 'failure' in responses[0].content
    finally:
        vasp.pp.stop_processor()



def test_vasp_response(simple_response_json_error, vasp, other_addr):
    vasp.process_response(other_addr, simple_response_json_error)

def test_sample_vasp_info_is_authorised(request, asset_path):
    cert_file = Path(request.fspath).resolve()
    cert_file = cert_file.parents[3] / 'test_vectors' / 'client_cert.pem'
    cert_file = cert_file.resolve()
    with open(cert_file, 'rt') as f:
        cert_str = f.read()
    cert = OpenSSL.crypto.load_certificate(
        OpenSSL.crypto.FILETYPE_PEM, cert_str
    )
    my_addr   = LibraAddress.encode_to_Libra_address(b'B'*16)
    other_addr = LibraAddress.encode_to_Libra_address(b'A'*16)
    vc = sample_vasp(my_addr, asset_path)
    assert vc.info_context.is_authorised_VASP(cert, other_addr)
