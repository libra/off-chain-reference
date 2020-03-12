from payment_logic import *
from payment import *
from protocol_messages import *
from protocol import *
from business import BusinessAsyncInterupt
from utils import *
from libra_address import *

from unittest.mock import MagicMock
import pytest


@pytest.fixture
def basic_payment():
    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    action = PaymentAction(Decimal('10.00'), 'TIK', 'charge', '2020-01-02 18:00:00 UTC')
    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)
    return payment


def test_payment_command_serialization_net(basic_payment):
    cmd = PaymentCommand(basic_payment)
    data = cmd.get_json_data_dict(JSON_NET)
    cmd2 = PaymentCommand.from_json_data_dict(data, JSON_NET)
    assert cmd == cmd2


def test_payment_command_serialization_store(basic_payment):
    cmd = PaymentCommand(basic_payment)
    data = cmd.get_json_data_dict(JSON_STORE)
    cmd2 = PaymentCommand.from_json_data_dict(data, JSON_STORE)
    assert cmd == cmd2


def test_payment_end_to_end_serialization(basic_payment):
    # Define a full request/reply with a Payment and test serialization
    CommandRequestObject.register_command_type(PaymentCommand)

    cmd = PaymentCommand(basic_payment)
    request = CommandRequestObject(cmd)
    request.seq = 10
    request.response = make_success_response(request)
    data = request.get_json_data_dict(JSON_STORE)
    request2 = CommandRequestObject.from_json_data_dict(data, JSON_STORE)
    assert request == request2


def test_payment_command_multiple_dependencies_fail(basic_payment):
    new_payment = basic_payment.new_version('v1')
    new_payment.extends += ['v2']
    cmd = PaymentCommand(new_payment)
    with pytest.raises(PaymentLogicError):
        cmd.get_object(None, [basic_payment])


def test_payment_command_create_fail(basic_payment):
    cmd = PaymentCommand(basic_payment)
    cmd.creates += [basic_payment.get_version()]
    with pytest.raises(PaymentLogicError):
        cmd.get_object(None, [])


def test_payment_command_missing_dependency_fail(basic_payment):
    new_payment = basic_payment.new_version('v1')
    cmd = PaymentCommand(new_payment)
    with pytest.raises(PaymentLogicError):
        cmd.get_object(None, [])


# ----- check_new_payment -----


def test_payment_create_from_recipient(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]

    pp = PaymentProcessor(bcm)
    pp.check_new_payment(basic_payment)
    

def test_payment_create_from_sender_sig_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]
    bcm.validate_recipient_signature.side_effect = [BusinessValidationFailure('Sig fails')]

    pp = PaymentProcessor(bcm)
    with pytest.raises(BusinessValidationFailure):
        pp.check_new_payment(basic_payment)



def test_payment_create_from_sender(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]
    pp = PaymentProcessor(bcm)
    pp.check_new_payment(basic_payment)



def test_payment_create_from_sender_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]

    basic_payment.data['receiver'].update({'status': Status.ready_for_settlement})
    pp = PaymentProcessor(bcm)
    with pytest.raises(PaymentLogicError):
        pp.check_new_payment(basic_payment)


def test_payment_create_from_receiver_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]

    basic_payment.data['sender'].update({'status': Status.ready_for_settlement})
    basic_payment.data['receiver'].update({'status': Status.ready_for_settlement})
    pp = PaymentProcessor(bcm)
    with pytest.raises(PaymentLogicError):
        pp.check_new_payment(basic_payment)


def test_payment_create_from_receiver_bad_state_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]

    basic_payment.data['receiver'].update({'status': Status.needs_recipient_signature})
    pp = PaymentProcessor(bcm)
    with pytest.raises(PaymentLogicError):
        pp.check_new_payment(basic_payment)


# ----- check_new_update -----


def test_payment_update_from_sender(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]
    pp = PaymentProcessor(bcm)
    diff = {}
    new_obj = basic_payment.new_version()
    new_obj = PaymentObject.from_full_record(diff, base_instance=new_obj)
    pp.check_new_update(basic_payment, new_obj)


def test_payment_update_from_sender_modify_receiver_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]
    diff = {'receiver': {'status': Status.settled}}
    new_obj = basic_payment.new_version()
    new_obj = PaymentObject.from_full_record(diff, base_instance=new_obj)
    pp = PaymentProcessor(bcm)
    assert new_obj.data['receiver'].data['status'] != basic_payment.data['receiver'].data['status']
    with pytest.raises(PaymentLogicError):
        pp.check_new_update(basic_payment, new_obj)


def test_payment_update_from_receiver_invalid_state_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]
    pp = PaymentProcessor(bcm)
    diff = {'receiver': {'status': Status.needs_recipient_signature}}
    new_obj = basic_payment.new_version()
    new_obj = PaymentObject.from_full_record(diff, base_instance=new_obj)
    with pytest.raises(PaymentLogicError):
        pp.check_new_update(basic_payment, new_obj)


def test_payment_update_from_receiver_invalid_transition_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]
    pp = PaymentProcessor(bcm)
    basic_payment.data['receiver'].update({'status': Status.ready_for_settlement})
    diff = {'receiver': {'status': Status.needs_kyc_data}}
    new_obj = basic_payment.new_version()
    new_obj = PaymentObject.from_full_record(diff, base_instance=new_obj)
    with pytest.raises(PaymentLogicError):
        pp.check_new_update(basic_payment, new_obj)


def test_payment_update_from_receiver_unilateral_abort_fail(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [False]
    pp = PaymentProcessor(bcm)
    basic_payment.data['receiver'].update({'status': Status.ready_for_settlement})
    diff = {'receiver': {'status': Status.abort}}
    new_obj = basic_payment.new_version()
    new_obj = PaymentObject.from_full_record(diff, base_instance=new_obj)
    with pytest.raises(PaymentLogicError):
        pp.check_new_update(basic_payment, new_obj)


# ----- payment_process -----
@pytest.fixture(params=[
    ('AAAA', 'BBBB', 'AAAA', True),
    ('BBBB', 'AAAA', 'AAAA', True),
    ('CCCC', 'AAAA', 'AAAA', False),
    ('BBBB', 'CCCC', 'AAAA', False),
    ('DDDD', 'CCCC', 'AAAA', False),
    ('AAAA', 'BBBB', 'BBBB', True),
    ('BBBB', 'AAAA', 'DDDD', False),
])
def states(request):
    return request.param

def test_payment_processor_check(states, basic_payment):
    src_addr, dst_addr, origin_addr, res = states
    bcm = MagicMock(spec=BusinessContext)
    vasp = MagicMock()
    channel = MagicMock()
    channel.other.plain.side_effect = [ src_addr ] 
    channel.myself.plain.side_effect = [ dst_addr ] 
    executor = MagicMock()
    own = False
    command = PaymentCommand(basic_payment)
    origin = MagicMock(spec=LibraAddress)
    origin.plain.return_value = origin_addr
    command.set_origin(origin)
    pp = PaymentProcessor(bcm)

    if res:
        pp.check_command(vasp, channel, executor, command, own)
    else:
        with pytest.raises(PaymentLogicError):
            pp.check_command(vasp, channel, executor, command, own)

def test_payment_process_receiver_new_payment(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True, True]
    bcm.check_account_existence.side_effect = [None]
    bcm.next_kyc_level_to_request.side_effect = [Status.needs_kyc_data]
    bcm.next_kyc_to_provide.side_effect = [{Status.none}]
    bcm.ready_for_settlement.side_effect = [False]

    assert basic_payment.data['receiver'].data['status'] == Status.none
    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)

    assert new_payment.data['receiver'].data['status'] == Status.needs_kyc_data

    new_payment.data['receiver'].data['status'] == Status.ready_for_settlement
    bcm.is_recipient.side_effect = [True, True]
    bcm.check_account_existence.side_effect = [None]
    bcm.next_kyc_level_to_request.side_effect = [Status.none]
    bcm.next_kyc_to_provide.side_effect = [{Status.none}]
    bcm.ready_for_settlement.side_effect = [True]
    bcm.want_single_payment_settlement.side_effect = [True]
    bcm.has_settled.side_effect = [False]

    pp = PaymentProcessor(bcm)
    new_payment2 = pp.payment_process(new_payment)
    assert new_payment2.data['receiver'].data['status'] == Status.ready_for_settlement

    bcm.is_recipient.side_effect = [True, True]
    bcm.check_account_existence.side_effect = [None]
    bcm.next_kyc_level_to_request.side_effect = [Status.none]
    bcm.next_kyc_to_provide.side_effect = [{Status.none}]
    bcm.ready_for_settlement.side_effect = [True]
    bcm.want_single_payment_settlement.side_effect = [True]
    bcm.has_settled.side_effect = [True]

    pp = PaymentProcessor(bcm)
    new_payment3 = pp.payment_process(new_payment2)
    assert new_payment3.data['receiver'].data['status'] == Status.settled


def test_payment_process_interrupt(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True, True]
    bcm.check_account_existence.side_effect = [None]
    bcm.next_kyc_level_to_request.side_effect = [BusinessAsyncInterupt(1234)]

    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)
    assert not new_payment.has_changed()
    assert new_payment.data['receiver'].data['status'] == Status.none


def test_payment_process_interrupt_resume(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True, True, True, True]
    bcm.check_account_existence.side_effect = [None, None]
    bcm.next_kyc_level_to_request.side_effect = [Status.ready_for_settlement]
    bcm.next_kyc_to_provide.side_effect = [BusinessAsyncInterupt(1234)]

    pp = PaymentProcessor(bcm)
    assert basic_payment.data['receiver'].data['status'] == Status.none
    new_payment = pp.payment_process(basic_payment)
    assert new_payment.has_changed()
    assert new_payment.data['receiver'].data['status'] == Status.ready_for_settlement

    bcm.next_kyc_to_provide.side_effect = [set()]
    bcm.ready_for_settlement.side_effect = [True]
    bcm.has_settled.side_effect = [True]

    pp.notify_callback(1234)
    L = pp.payment_process_ready()
    assert len(L) == 1
    assert len(pp.callbacks) == 0
    assert len(pp.ready) == 0
    print(bcm.method_calls)
    L[0].data['receiver'].data['status'] == Status.settled


def test_payment_process_abort(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True, True]
    bcm.check_account_existence.side_effect = [None]
    bcm.next_kyc_level_to_request.side_effect = [BusinessForceAbort]

    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)
    assert new_payment.data['receiver'].data['status'] == Status.abort


def test_payment_process_abort_from_sender(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]
    bcm.ready_for_settlement.side_effect = [False]
    basic_payment.data['sender'].data['status'] = Status.abort
    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)
    assert new_payment.data['receiver'].data['status'] == Status.abort


def test_payment_process_get_stable_id(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]
    bcm.next_kyc_to_provide.side_effect = [Status.needs_stable_id]
    bcm.get_stable_id.side_effect = ['stable_id']
    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)
    assert new_payment.data['receiver'].data['stable_id'] == 'stable_id'


def test_payment_process_get_extended_kyc(basic_payment):
    bcm = MagicMock(spec=BusinessContext)
    bcm.is_recipient.side_effect = [True]
    bcm.next_kyc_to_provide.side_effect = [Status.needs_kyc_data]
    kyc_data = KYCData('{"payment_reference_id": "123", "type": "A"}')
    bcm.get_extended_kyc.side_effect = [
        (kyc_data, 'sig', 'cert')
    ]
    bcm.ready_for_settlement.side_effect = [Status.ready_for_settlement]
    pp = PaymentProcessor(bcm)
    new_payment = pp.payment_process(basic_payment)
    assert new_payment.data['receiver'].data['kyc_data'] == kyc_data
    assert new_payment.data['receiver'].data['kyc_signature'] == 'sig'
    assert new_payment.data['receiver'].data['kyc_certificate'] == 'cert'
