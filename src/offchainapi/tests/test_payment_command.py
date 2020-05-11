from ..sample.sample_command import SampleCommand
from ..payment_command import PaymentCommand, PaymentLogicError
from ..protocol_messages import CommandRequestObject, make_success_response
from ..utils import JSONFlag, JSONSerializable

import pytest

def test_payment_command_serialization_net(payment):
    cmd = PaymentCommand(payment)
    data = cmd.get_json_data_dict(JSONFlag.NET)
    cmd2 = PaymentCommand.from_json_data_dict(data, JSONFlag.NET)
    assert cmd == cmd2


def test_payment_command_serialization_parse(payment):
    cmd = PaymentCommand(payment)
    data = cmd.get_json_data_dict(JSONFlag.NET)
    obj = JSONSerializable.parse(data, JSONFlag.NET)
    assert obj == cmd

    cmd_s = SampleCommand('Hello', deps=['World'])
    data2 = cmd_s.get_json_data_dict(JSONFlag.NET)
    cmd_s2 = JSONSerializable.parse(data2, JSONFlag.NET)
    assert cmd_s == cmd_s2


def test_payment_command_serialization_store(payment):
    cmd = PaymentCommand(payment)
    data = cmd.get_json_data_dict(JSONFlag.STORE)
    cmd2 = PaymentCommand.from_json_data_dict(data, JSONFlag.STORE)
    assert cmd == cmd2


def test_payment_end_to_end_serialization(payment):
    # Define a full request/reply with a Payment and test serialization
    cmd = PaymentCommand(payment)
    request = CommandRequestObject(cmd)
    request.seq = 10
    request.response = make_success_response(request)
    data = request.get_json_data_dict(JSONFlag.STORE)
    request2 = CommandRequestObject.from_json_data_dict(data, JSONFlag.STORE)
    assert request == request2


def test_payment_command_multiple_dependencies_fail(payment):
    new_payment = payment.new_version('v1')
    # Error: 2 dependencies
    new_payment.previous_versions += ['v2']
    cmd = PaymentCommand(new_payment)
    with pytest.raises(PaymentLogicError):
        cmd.get_object(new_payment.get_version(),
                       {payment.get_version(): payment})


def test_payment_command_missing_dependency_fail(payment):
    new_payment = payment.new_version('v1')
    cmd = PaymentCommand(new_payment)
    with pytest.raises(PaymentLogicError):
        cmd.get_object(new_payment.get_version(), {})