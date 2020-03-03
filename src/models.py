from copy import deepcopy
from business import ProtocolExecutor

class OffChainVASP:
    """Manages the off-chain protocol on behalf of one VASP"""
    pass


class VASPInfo:
    """Contains information about VASPs"""

    def get_base_url(self):
        """ Base URL that manages off-chain communications"""
        pass

    def is_authorised_VASP(self):
        """ Whether this has the authorised VASP bit set on chain"""
        pass

    def get_libra_address(self):
        """ The settlement Libra address for this channel"""
        pass

    def get_parent_address(self):
        """ The VASP Parent address for this channel. High level logic is common
        to all Libra addresses under a parent to ensure consistency and compliance."""
        pass

    def get_TLS_certificate(self):
        """ TODO: Get the on-chain TLS certificate to authenticate channels. """
        pass

    def verify_signature(self, message, signature):
        """ Verify a message and signature to ensure it was sent by this VASP."""
        pass


class VASPPairChannel:
    """Represents the state of an off-chain bi-directional channel bewteen two VASPs"""

    def __init__(self, myself, other, CommandExecutor = None):
        """ Initialize the channel between two VASPs.

        * Myself is the VASP initializing the local object (VASPInfo)
        * Other is the other VASP (VASPInfo).
        """

        self.myself = myself
        self.other = other

        self.pending_requests = []

        # TODO[issue #7]: persist and recover the command sequences
        self.my_requests = []
        self.my_next_seq = 0
        self.other_requests = []
        self.other_next_seq = 0

        # The final sequence
        self.executor = ProtocolExecutor()


    def next_final_sequence(self):
        return self.executor.next_seq()

    def get_final_sequence(self):
        return self.executor.seq

    def persist(self):
        """ A hook to block until state of channel is persisted """
        pass

    def send_request(self, request):
        """ A hook to send a message to other VASP"""
        pass

    def send_response(self, response):
        pass

    def get_protocol_version(self):
        """ Returns the protocol version of this channel."""
        pass

    def is_client(self):
        """ Is the local VASP a client for this pair?"""
        myself_address = self.myself.get_parent_address()
        other_address = self.other.get_parent_address()

        # Write out the logic, for clarity
        bit = myself_address.last_bit() ^ other_address.last_bit()
        if bit == 0:
            return myself_address.greater_than_or_equal(other_address)
        if bit == 1:
            return not myself_address.greater_than_or_equal(other_address)
        assert False  # Never reach this code

    def role(self):
        return ['Client','Server'][self.is_server()]

    def is_server(self):
        """ Is the local VASP a server for this pair?"""
        return not self.is_client()

    def pending_responses(self):
        """ Counts the number of responses this VASP is waiting for """
        return len([1 for req in self.my_requests if not req.has_response()])

    def process_pending_requests(self):
        """ The server re-schedules and executes pending requests """
        if self.pending_responses() == 0:
            requests = self.pending_requests
            self.pending_requests = []
            for req in requests:
                self.handle_request(req)


    def apply_response_to_executor(self, request):
        assert request.response is not None
        response = request.response
        if request.is_success():
            self.executor.set_success(response.command_seq)
        else:
            self.executor.set_fail(response.command_seq)

    def sequence_command_local(self, off_chain_command):
        """ The local VASP attempts to sequence a new off-chain command."""

        request = CommandRequestObject(off_chain_command)
        request.seq = self.my_next_seq

        if self.is_server():
            # TODO: Handle protocol failures if raised
            #       bail if it fails here.
            request.command_seq = self.next_final_sequence()
            self.executor.sequence_next_command(off_chain_command,
                do_not_sequence_errors = True, own=True)

        self.my_next_seq += 1
        self.my_requests += [request]
        self.send_request(request)
        self.persist()


    def handle_request(self, request):
        """ Handles a request received by this VASP """
        # Always answer old requests
        if request.seq < self.other_next_seq:
            previous_request = self.other_requests[request.seq]
            if previous_request.is_same_command(request):
                # Re-send the response
                response = previous_request.response
                self.send_response(response)
                return
            else:
                # There is a conflict, and it will have to be resolved
                #  TODO[issue 8]: How are conflicts meant to be resolved? With only
                #        two participants we cannot tolerate errors.
                response = make_protocol_error(request, code='conflict')
                response.previous_command = previous_request.command
                self.send_response(response)
                return

        # Clients are not to suggest sequence numbers.
        if self.is_server() and request.command_seq is not None:
            response = make_protocol_error(request, code='malformed')
            self.send_response(response)
            return

        # As a server we first wait for the status of all server
        # requests to sequence any new client requests.
        if self.is_server() and self.pending_responses() > 0:
            self.pending_requests += [request]
            return

        # Sequence newer requests
        if request.seq == self.other_next_seq:

            if self.is_client() and request.command_seq > self.next_final_sequence():
                # We must wait, since we cannot give an answer before sequencing
                # previous commands.
                response = make_protocol_error(request, code='wait')
                self.send_response(response)
                return

            self.other_next_seq += 1
            self.other_requests += [request]

            # TODO: prove this assertion or we should throw an error if it is
            #       not true.
            if request.command_seq is not None:
                assert request.command_seq == self.next_final_sequence()

            # request.response.command_seq = self.next_final_sequence()
            # TODO: if this fails, inlcude a command failure in response.
            # TODO: Add response of failure


            seq = self.next_final_sequence()
            try:
                self.executor.sequence_next_command(request.command, \
                    do_not_sequence_errors = False, own=False)
                response = make_success_response(request)
            except Exception as e:
                response = make_command_error(request, e)

            request.response = response
            request.response.command_seq = seq
            self.apply_response_to_executor(request)

            self.persist()
            self.send_response(request.response)

        elif request.seq > self.other_next_seq:
            # We have received the other side's request without receiving the
            # previous one
            response = make_protocol_error(request, code='missing')
            self.send_response(response)
        else:
            assert False

    def handle_response(self, response):
        """ Handles a response to a request by this VASP """
        request_seq = response.seq
        assert isinstance(request_seq, int)
        assert isinstance(response, CommandResponseObject)

        # Check this is the next expected response
        if not request_seq < len(self.my_requests):
            # Caught a bug on the other side
            # TODO: Log warning the other side might be buggy
            return

        if request_seq > 0:
            if not self.my_requests[request_seq - 1].has_response():
                # TODO: the other side is buggy, log a warning
                return

        if response.status == 'success' or (
                response.status == 'failure' and not response.error.protocol_error):

            # Idenpotent: We have already processed the response
            if self.my_requests[request_seq].has_response():
                # TODO: Check the reponse is the same and log warning otherwise.
                return

            request = self.my_requests[request_seq]
            if response.command_seq == self.next_final_sequence():
                # Next command to commit -- do commit it.
                request.response = response

                try:
                    self.executor.sequence_next_command(request.command, \
                        do_not_sequence_errors = False, own=True)
                except:
                    # We ignore the outcome since the response is what matters.
                    # TODO: something buggy has happened, if we return an error
                    #       at this point. Log a warning to interop testing.
                    pass

                self.apply_response_to_executor(request)
                self.process_pending_requests()
                self.persist()

            elif response.command_seq < self.next_final_sequence():
                # Request already in the sequence: happens to the leader.
                #  No chance to register an error, since we do not reply.
                request.response = response
                self.apply_response_to_executor(request)
                self.process_pending_requests()

            elif response.command_seq > self.next_final_sequence():
                # This is too high -- wait for more data?
                # Never happens if both sides are correct.
                # TODO: if this happens the other side may be buggy. Log a warning.
                pass
            else:
                # Previous conditions are exhaustive
                assert False
        else:
            # Handle protocol failures.
            if response.error.code == 'missing':
                pass  # Will Retransmit
            elif response.error.code == 'wait':
                pass  # Will Retransmit
            elif response.error.code == 'malformed':
                pass # Implementation bug was caught.
            else:
                # Manage other errors
                assert False

    def retransmit(self):
        """ Re-sends the earlierst request that has not yet got a response, if any """
        for request in self.my_requests:
            assert isinstance(request, CommandRequestObject)
            if not request.has_response():
                self.send_request(request)
                break

    def would_retransmit(self):
        """ Returns true if there are any pending re-transmits, namely
            requests for which the response has not yet been received. """
        for request in self.my_requests:
            assert isinstance(request, CommandRequestObject)
            if not request.has_response():
                return True
        return False


class OffChainError:
    def __init__(self, protocol_error=True, code=None):
        self.protocol_error = protocol_error
        self.code = code


class CommandRequestObject:
    """Represents a command of the Off chain protocol"""

    def __init__(self, command):
        self.seq = None         # The sequence in the local queue
        self.command_seq = None  # Only server sets this
        self.command = command

        # Indicates whether the command was been confirmed by the other VASP
        self.response = None

    def is_same_command(self, other):
        """ Returns true if the other command is the same as this one,
            Used to detect conflicts in case of buggy corresponding VASP."""
        return self.command == other.command

    def has_response(self):
        return self.response is not None

    def is_success(self):
        assert self.has_response()
        return self.response.status == 'success'


class CommandResponseObject:
    """Represents a response to a command in the Off chain protocol"""

    def __init__(self):
        # Start with no data
        self.seq = None
        self.command_seq = None
        self.status = None
        self.error = None


def make_success_response(request):
    response = CommandResponseObject()
    response.seq = request.seq
    response.status = 'success'
    return response


def make_protocol_error(request, code=None):
    response = CommandResponseObject()
    response.seq = request.seq
    response.status = 'failure'
    response.error = OffChainError(protocol_error=True, code=code)
    return response

def make_command_error(request, code=None):
    response = CommandResponseObject()
    response.seq = request.seq
    response.status = 'failure'
    response.error = OffChainError(protocol_error=False, code=code)
    return response


# Helper classes
class LibraAddress:
    """ An interface that abstracts a Libra Address and bit manipulations on it."""

    def last_bit(self):
        pass

    def greater_than_or_equal(self, other):
        pass

    def equal(self, other):
        pass
