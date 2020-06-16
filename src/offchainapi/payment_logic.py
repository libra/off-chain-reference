from .business import BusinessForceAbort
from .executor import CommandProcessor, ProtocolCommand
from .payment import Status, PaymentObject
from .status_logic import status_heights_MUST, \
    is_valid_status_transition, is_valid_initial
from .payment_command import PaymentCommand, PaymentLogicError
from .asyncnet import NetworkException
from .shared_object import SharedObject
from .libra_address import LibraAddress

import asyncio
import logging
import json


logger = logging.getLogger(name='libra_off_chain_api.payment_logic')


class PaymentProcessor(CommandProcessor):
    ''' The logic to process a payment from either side.

    The processor checks commands as they are received from the other
    VASP. When a command from the other VASP is successful it is
    passed on to potentially lead to a further command. It is also
    notified of sequenced commands that failed, and the error that
    lead to that failure.

    Crash-recovery strategy: The processor must only process each
    command once. For this purpose the Executor passes commands
    in the order they have been sequenced by the lower-level
    protocol on each channel, and does so only once for each command
    in the sequence for each channel.

    The Processor must store those commands, and ensure they have
    all been suitably processed upon a potential crash and recovery.
    '''

    def __init__(self, business, storage_factory, loop=None):
        self.business = business

        # Asyncio support
        self.loop = loop
        self.net = None

        # The processor state -- only access through event loop to prevent
        # mutlithreading bugs.
        self.storage_factory = storage_factory
        with self.storage_factory.atomic_writes():
            root = storage_factory.make_value('processor', None)
            self.reference_id_index = storage_factory.make_dict(
                'reference_id_index', PaymentObject, root)

            # This is the primary store of shared objects.
            # It maps version numbers -> objects.
            self.object_store = storage_factory.make_dict(
                'object_store', SharedObject, root=root)

            # TODO: how much of this do we want to persist?
            self.pending_commands = storage_factory.make_dict(
                'pending_commands', str, root)
            self.command_cache = storage_factory.make_dict(
                'command_cache', ProtocolCommand, root)

        # Storage for debug futures list
        self.futs = []

    def set_network(self, net):
        ''' Assigns a concrete network for this command processor to use. '''
        assert self.net is None
        self.net = net

    # ------ Machinery for crash tolerance.

    def command_unique_id(self, other_str, seq):
        ''' Returns a string that uniquerly identifies this
            command for the local VASP.'''
        data = json.dumps((other_str, seq))
        return f'{other_str}_{seq}', data

    def persist_command_obligation(self, other_str, seq, command):
        ''' Persists the command to ensure its future execution. '''
        uid, data = self.command_unique_id(other_str, seq)
        self.pending_commands[uid] = data
        self.command_cache[uid] = command

    def obligation_exists(self, other_str, seq):
        uid, _ = self.command_unique_id(other_str, seq)
        return uid in self.pending_commands

    def release_command_obligation(self, other_str, seq):
        ''' Once the command is executed, and a potential response stored,
            this function allows us to remove the obligation to process
            the command. '''
        uid, _ = self.command_unique_id(other_str, seq)
        del self.pending_commands[uid]
        del self.command_cache[uid]

    def list_command_obligations(self):
        ''' Returns a list of (other_address, command sequence) tuples denoting
            the pending commands that need to be re-executed after a crash or
            shutdown. '''
        pending = []
        for uid in self.pending_commands.keys():
            data = self.pending_commands[uid]
            (other_address_str, seq) = json.loads(data)
            command = self.command_cache[uid]
            pending += [(other_address_str, command, seq)]
        return pending

    async def retry_process_commands(self):
        ''' A coroutine that attempts to re-processes any pending commands
            after recovering from a crash. '''

        pending_commands = self.list_command_obligations()

        logger.info(
            f"Re-scheduling {len(pending_commands)} commands for processing"
        )
        new_tasks = []
        for (other_address_str, command, seq) in pending_commands:
            other_address = LibraAddress(other_address_str)
            task = self.loop.create_task(self.process_command_success_async(
                other_address, command, seq))
            new_tasks += [task]

        return new_tasks

    # ------ Machinery for supporting async Business context ------

    async def process_command_failure_async(
            self, other_address, command, seq, error):
        ''' Process any command failures from either ends of a channel.'''
        logger.error(
            f"(other:{other_address.as_str()}) Command #{seq} Failure: {error}"
        )

    async def process_command_success_async(self, other_address, command, seq):
        """ The asyncronous command processing logic.

        Checks all incomming commands from the other VASP, and determines if
        any new commands need to be issued from this VASP in response.

        Args:
            other_address (LibraAddress):  The other VASP address in the
                channel that received this command.
            command (PaymentCommand): The current payment command.
            seq (int): The sequence number of the payment command.
        """
        # To process commands we should have set a network
        if self.net is None:
            raise PaymentLogicError(
                'Setup a processor network to process commands.'
            )

        # If there is no registered obligation to process there is no
        # need to process this command. We log here an error, which
        # might be due to a bug.
        other_address_str = other_address.as_str()
        if not self.obligation_exists(other_address_str, seq):
            logger.error(
                f"(other:{other_address_str}) "
                f"Process command called without obligation #{seq}"
            )
            return

        logger.info(f"(other:{other_address_str}) Process Command #{seq}")

        try:
            # Only respond to commands by other side.
            if command.origin == other_address:

                # Determine if we should inject a new command.
                payment = command.get_payment(self.object_store)
                new_payment = await self.payment_process_async(payment)

                if new_payment.has_changed():
                    new_cmd = PaymentCommand(new_payment)

                    # This context ensure that either we both
                    # write the next request & free th obligation
                    # Or none of the two.
                    with self.storage_factory.atomic_writes():
                        request = self.net.sequence_command(
                            other_address, new_cmd
                        )

                        # Crash-recovery: Once a request is ordered to
                        # be sent out we can consider this command
                        # done.
                        if self.obligation_exists(other_address_str, seq):
                            self.release_command_obligation(
                                other_address_str, seq)

                    # Attempt to send it to the other VASP.
                    await self.net.send_request(other_address, request)
                else:
                    logger.debug(
                        f"(other:{other_address_str}) No more commands "
                        f"created for Payment lastly with seq num #{seq}"
                    )

            # If we are here we are done with this obligation.
            with self.storage_factory.atomic_writes():
                if self.obligation_exists(other_address_str, seq):
                    self.release_command_obligation(other_address_str, seq)

        # Prevent the next catch-all handler from catching canceled exceptions.
        except asyncio.CancelledError as e:
            raise e

        except NetworkException as e:
            logger.warning(
                f"(other:{other_address_str}) Network error: seq #{seq}: {str(e)}"
            )
        except Exception as e:
            logger.error(
                f"(other:{other_address_str}) "
                f"Payment processing error: seq #{seq}: {str(e)}",
                exc_info=True,
            )

    # -------- Implements CommandProcessor interface ---------

    def business_context(self):
        ''' Overrides CommandProcessor. '''
        return self.business

    def check_command(self, channel, command):
        ''' Overrides CommandProcessor. '''

        dependencies = self.object_store
        new_version = command.get_new_version()
        new_payment = command.get_object(new_version, dependencies)

        # Ensure that the two parties involved are in the VASP channel
        parties = set([
            new_payment.sender.get_address().as_str(),
            new_payment.receiver.get_address().as_str(),
        ])

        needed_parties = set([
            channel.get_my_address().as_str(),
            channel.get_other_address().as_str()
        ])

        if parties != needed_parties:
            raise PaymentLogicError(
                f'Wrong Parties: expected {needed_parties} '
                f'but got {str(parties)}'
            )

        other_addr = channel.get_other_address().as_str()

        # Ensure the originator is one of the VASPs in the channel.
        origin = command.get_origin().as_str()
        if origin not in parties:
            raise PaymentLogicError('Command originates from wrong party')

        # Only check the commands we get from others.
        if origin == other_addr:
            if command.dependencies == []:

                # Check that the reference_id is correct
                # Only do this for the definition of new payments, after that
                # the ref id stays the same.

                ref_id_structure = new_payment.reference_id.split('_')
                if not (len(ref_id_structure) > 1 and ref_id_structure[0] == origin):
                    raise PaymentLogicError(
                        f'Expected reference_id of the form {origin}_XYZ, got: '
                        f'{new_payment.reference_id}'
                    )

                self.check_new_payment(new_payment)
            else:
                old_version = command.get_previous_version()
                old_payment = dependencies[old_version]
                self.check_new_update(old_payment, new_payment)

    def process_command(self, vasp, channel, executor, command,
                        seq, status_success, error=None):
        ''' Overrides CommandProcessor. '''

        other_addr = channel.get_other_address()
        other_str = other_addr.as_str()

        # Call the failure handler and exit.
        if not status_success:
            fut = self.loop.create_task(self.process_command_failure_async(
                other_addr, command, seq, error)
            )
            if __debug__:
                self.futs += [fut]
            return fut

        # Creates new objects.
        new_versions = command.new_object_versions()
        for version in new_versions:
            obj = command.get_object(version, self.object_store)
            self.object_store[version] = obj

        # Update the Index of Reference ID -> Payment.
        self.store_latest_payment_by_ref_id(command)

        # We record an obligation to process this command, even
        # after crash recovery.
        self.persist_command_obligation(other_str, seq, command)

        # Spin further command processing in its own task.
        logger.debug(f"(other:{other_addr.as_str()}) Schedule cmd {seq}")
        fut = self.loop.create_task(self.process_command_success_async(
            other_addr, command, seq))

        # Log the futures here to execute them inidividually
        # when testing.
        if __debug__:
            self.futs += [fut]

        return fut

    # -------- Get Payment API commands --------

    def get_latest_payment_by_ref_id(self, ref_id):
        ''' Returns the latest payment version with
            the reference ID provided.'''
        if ref_id in self.reference_id_index:
            return self.reference_id_index[ref_id]
        else:
            raise KeyError(ref_id)

    def get_payment_history_by_ref_id(self, ref_id):
        ''' Generator that returns all versions of a
            payment with a given reference ID
            in reverse causal order (newest first). '''
        payment = self.get_latest_payment_by_ref_id(ref_id)
        yield payment

        while len(payment.previous_versions) > 0:
            p_version = payment.previous_versions[0]
            payment = self.object_store[p_version]
            yield payment

    def store_latest_payment_by_ref_id(self, command):
        ''' Internal command to update the payment index '''
        payment = command.get_payment(self.object_store)

        # Update the Index of Reference ID -> Payment.
        ref_id = payment.reference_id

        # Write the new payment to the index of payments by
        # reference ID to support they GetPaymentAPI.
        if ref_id in self.reference_id_index:
            # We get the dependencies of the old payment.
            old_version = self.reference_id_index[ref_id].get_version()

            # We check that the previous version is present.
            # If so we update it with the new one.
            dependencies_versions = command.get_dependencies()
            if old_version in dependencies_versions:
                self.reference_id_index[ref_id] = payment
        else:
            self.reference_id_index[ref_id] = payment

    # ----------- END of CommandProcessor interface ---------

    def check_signatures(self, payment):
        ''' Utility function that checks all signatures present for validity. '''
        business = self.business
        is_sender = business.is_sender(payment)
        other_actor = payment.receiver if is_sender else payment.sender

        if is_sender and 'recipient_signature' in payment:
            business.validate_recipient_signature(payment)

    def check_new_payment(self, new_payment):
        ''' Checks a diff for a new payment from the other VASP, and returns
            a valid payemnt. If a validation error occurs, then an exception
            is thrown.

            NOTE: the VASP may be the RECEIVER of the new payment, for example
            for person to person payment initiated by the sender. The VASP
            may also be the SENDER for the payment, such as in cases where a
            merchant is charging an account, a refund, or a standing order.`

            The only real check is that that status for the VASP that has
            not created the payment must be none, to allow for checks and
            potential aborts. However, KYC information on both sides may
            be included by the other party, and should be checked.
        '''
        business = self.business
        is_receipient = business.is_recipient(new_payment)
        is_sender = not is_receipient

        # Ensure address and subaddress are consistent
        sender_addr = LibraAddress(new_payment.sender.get_address().as_str(),)
        sender_subaddr = LibraAddress(new_payment.sender.address)
        recv_addr = LibraAddress(new_payment.receiver.get_address().as_str(),)
        recv_subaddr = LibraAddress(new_payment.receiver.address)

        if sender_subaddr.onchain() != sender_addr or \
                recv_subaddr.onchain() != recv_addr:
            raise PaymentLogicError(
                'Address and subaddress mismatch.'
            )

        role = ['sender', 'receiver'][is_receipient]
        other_role = ['sender', 'receiver'][is_sender]
        other_status = new_payment.data[other_role].status
        if new_payment.data[role].status != Status.none:
            raise PaymentLogicError(
                'Sender set receiver status or vice-versa.'
            )

        if not self.good_initial_status(new_payment, not is_sender):
            raise PaymentLogicError('Invalid status transition.')

        # Check that the subaddreses are valid
        sub_send = LibraAddress(new_payment.sender.address)
        sub_revr = LibraAddress(new_payment.receiver.address)

        if sub_send.version != 1:
            raise PaymentLogicError('Sender Subaddress needs to contain'
                                    ' an encoded subaddress.')
        if sub_revr.version != 1:
            raise PaymentLogicError('Receiver Subaddress needs to contain'
                                    ' an encoded subaddress.')

        self.check_signatures(new_payment)

    def check_new_update(self, payment, new_payment):
        ''' Checks a diff updating an existing payment.

            On success returns the new payment object. All check are fast to
            ensure a timely response (cannot support async operations).
        '''
        business = self.business
        is_receiver = business.is_recipient(new_payment)
        is_sender = not is_receiver

        role = ['sender', 'receiver'][is_receiver]
        other_role = ['sender', 'receiver'][role == 'sender']
        myself_actor = payment.data[role]
        myself_actor_new = new_payment.data[role]
        other_actor = payment.data[other_role]

        # Ensure nothing on our side was changed by this update.
        if myself_actor != myself_actor_new:
            raise PaymentLogicError(f'Cannot change {role} information.')

        # Check the status transition is valid.
        status = myself_actor.status
        other_status = other_actor.status
        other_role_is_sender = not is_sender

        if not self.can_change_status(payment, other_status, other_role_is_sender):
            raise PaymentLogicError('Invalid Status transition')

        self.check_signatures(new_payment)

    def payment_process(self, payment):
        ''' A syncronous version of payment processing -- largely
            used for pytests '''
        loop = self.loop
        if self.loop is None:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.payment_process_async(payment))

    def can_change_status(self, payment, new_status, actor_is_sender):
        """ Checks whether an actor can change the status of a payment
            to a new status accoding to our logic for valid state
            transitions.

        Parameters:
            * payment (PaymentObject): the initial payment we are updating.
            * new_status (Status): the new status we want to transition to.
            * actor_is_sender (bool): whether the actor doing the transition
                is a sender (set False for receiver).

        Returns:
            * bool: True for valid transition and False otherwise.
        """

        old_sender = payment.sender.status
        old_receiver = payment.receiver.status
        new_sender, new_receiver = old_sender, old_receiver
        if actor_is_sender:
            new_sender = new_status
        else:
            new_receiver = new_status

        valid = is_valid_status_transition(
            old_sender, old_receiver,
            new_sender, new_receiver,
            actor_is_sender)
        return valid

    def good_initial_status(self, payment, actor_is_sender):
        """ Checks whether a payment has a valid initial status, given
            the role of the actor that created it. Returns a bool set
            to true if it is valid."""

        sender_st = payment.sender.status
        receiver_st = payment.receiver.status
        return is_valid_initial(sender_st, receiver_st, actor_is_sender)

    async def payment_process_async(self, payment):
        ''' Processes a payment that was just updated, and returns a
            new payment with potential updates. This function may be
            called multiple times for the same payment to support
            async business operations and recovery.

            If there is no update to the payment simply return None.
        '''
        business = self.business

        is_receiver = business.is_recipient(payment)
        is_sender = not is_receiver
        role = ['sender', 'receiver'][is_receiver]
        other_role = ['sender', 'receiver'][not is_receiver]

        status = payment.data[role].status
        current_status = status
        other_status = payment.data[other_role].status

        new_payment = payment.new_version()

        try:
            # We set our status as abort.
            if other_status == Status.abort:
                current_status = Status.abort

            if current_status == Status.none:
                await business.check_account_existence(new_payment)

            if current_status in {Status.none,
                                  Status.needs_kyc_data,
                                  Status.needs_recipient_signature}:

                # Request KYC -- this may be async in case
                # of need for user input
                next_kyc = await business.next_kyc_level_to_request(
                    new_payment)
                if next_kyc != Status.none:
                    current_status = next_kyc

                # Provide KYC -- this may be async in case
                # of need for user input
                kyc_to_provide = await business.next_kyc_to_provide(
                    new_payment)

                myself_new_actor = new_payment.data[role]

                if Status.needs_kyc_data in kyc_to_provide:
                    extended_kyc = await business.get_extended_kyc(new_payment)
                    myself_new_actor.add_kyc_data(extended_kyc)

                if Status.needs_recipient_signature in kyc_to_provide:
                    signature = await business.get_recipient_signature(
                        new_payment)
                    new_payment.add_recipient_signature(signature)

            # Check if we have all the KYC we need
            if current_status not in {
                    Status.ready_for_settlement,
                    Status.settled}:
                ready = await business.ready_for_settlement(new_payment)
                if ready:
                    current_status = Status.ready_for_settlement

            # Ensure both sides are past the finality barrier
            if current_status == Status.ready_for_settlement \
                    and self.can_change_status(payment, Status.settled, is_sender):
                if await business.has_settled(new_payment):
                    current_status = Status.settled

        except BusinessForceAbort:

            # We cannot abort once we said we are ready_for_settlement
            # or beyond. However we will catch a wrong change in the
            # check when we change status.
            if self.can_change_status(payment, Status.abort, is_sender):
                current_status = Status.abort

        # Do an internal consistency check:
        if not self.can_change_status(payment, current_status, is_sender):
            sender_status = payment.sender.status
            receiver_status = payment.receiver.status
            raise PaymentLogicError(
                f'Invalid status transition while processing '
                f'payment {payment.get_version()}: '
                f'(({sender_status}, {receiver_status})) -> {current_status} '
                f'SENDER={is_sender}'
            )

        new_payment.data[role].change_status(current_status)

        return new_payment
