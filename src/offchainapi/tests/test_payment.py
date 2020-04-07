import pytest

from ..payment import *
from ..utils import JSONFlag


@pytest.fixture
def basic_actor():
    actor = PaymentActor('AAAA', 'aaaa', Status.none, [])
    return actor

@pytest.fixture
def basic_payment():
    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    action = PaymentAction(10, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')

    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)
    return payment

def test_json_payment(basic_payment):
    import json
    data = json.dumps(basic_payment.get_json_data_dict(flag=JSONFlag.STORE))
    pay2 = PaymentObject.from_json_data_dict(json.loads(data), flag=JSONFlag.STORE)
    assert basic_payment == pay2

def test_json_payment_flags(basic_payment):
    import json
    basic_payment.actually_live = False
    basic_payment.potentially_live = True
    data = json.dumps(basic_payment.get_json_data_dict(flag=JSONFlag.STORE))
    pay2 = PaymentObject.from_json_data_dict(json.loads(data), flag=JSONFlag.STORE)
    assert pay2.potentially_live and not pay2.actually_live

    basic_payment.actually_live = True
    basic_payment.potentially_live = False
    data = json.dumps(basic_payment.get_json_data_dict(flag=JSONFlag.STORE))
    pay2 = PaymentObject.from_json_data_dict(json.loads(data), flag=JSONFlag.STORE)
    assert not pay2.potentially_live and pay2.actually_live

    assert basic_payment.version == pay2.version
    assert basic_payment.version is not None

def test_kyc_data_missing_payment_reference_fail():
    with pytest.raises(StructureException):
        kyc_data = KYCData('{"type": "A"}')


def test_kyc_data_missing_type_fail():
    with pytest.raises(StructureException):
        kyc_data = KYCData('{"payment_reference_id": "1234"}')


def test_payment_action_creation():
    action = PaymentAction(10, 'LBT', 'charge', '2020-01-01 19:00 UTC')

    with pytest.raises(StructureException):
        # Try negative payment, should fail
        _ = PaymentAction(-10, 'LBT', 'charge', '2020-01-01 19:00 UTC')


with pytest.raises(StructureException):
    # Try zero payment, should fail
    _ = PaymentAction(0, 'LBT', 'charge', '2020-01-01 19:00 UTC')

    with pytest.raises(StructureException):
        # Use floating point for value
        _ = PaymentAction(0.01, 'LBT', 'charge', '2020-01-01 19:00 UTC')

    with pytest.raises(StructureException):
        # Use int for currency
        _ = PaymentAction(10, 5, 'charge', '2020-01-01 19:00 UTC')

    with pytest.raises(StructureException):
        # Use wrong type for action
        _ = PaymentAction(10, 'LBT', 0, '2020-01-01 19:00 UTC')

    with pytest.raises(StructureException):
        # Use wrong type for timestamp
        _ = PaymentAction(10, 'LBT', 'charge', 0)


def test_payment_actor_creation():
    actor = PaymentActor('ABCD', 'XYZ', Status.none, [])

    with pytest.raises(StructureException):
        # Bad address type
        _ = PaymentActor(0, 'XYZ', Status.none, [])

    with pytest.raises(StructureException):
        # Bad subaddress type
        _ = PaymentActor('ABCD', 0, Status.none, [])

    with pytest.raises(StructureException):
        # Bad status type
        _ = PaymentActor('ABCD', 'XYZ', 0, [])

    with pytest.raises(StructureException):
        # Bad metadata type
        _ = PaymentActor('ABCD', 'XYZ', Status.none, 0)


def test_payment_actor_update_stable_id():
    actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
    actor.add_stable_id('AAAA')
    assert actor.data['stable_id'] == 'AAAA'

    with pytest.raises(StructureException):
        # Cannot add a new stable id
        actor.add_stable_id('BBBB')

    with pytest.raises(StructureException):
        # Wrong type of stable ID
        actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
        actor.add_stable_id(0)


def test_payment_actor_update_status():
    actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
    actor.change_status(Status.needs_kyc_data)
    actor.change_status(Status.ready_for_settlement)

    with pytest.raises(StructureException):
        actor.change_status(0)


def test_payment_actor_update_kyc():
    kyc = KYCData("""{
        "payment_reference_id" : "PAYMENT_XYZ",
        "type" : "individual",
        "other_field" : "other data"
    }""")

    actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
    actor.add_kyc_data(kyc, 'sigXXXX', 'certXXX')

    # We tolerate writing again strictly the same record
    actor.add_kyc_data(kyc, 'sigXXXX', 'certXXX')

    with pytest.raises(StructureException):
        # Cannot change KYC data once set
        actor.add_kyc_data(kyc, 'sigYYYY', 'certYYYY')

    with pytest.raises(StructureException):
        # Wrong type for kyc data
        actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
        actor.add_kyc_data(0, 'sigXXXX', 'certXXX')

    with pytest.raises(StructureException):
        # Wrong type for sig data
        actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
        actor.add_kyc_data(kyc, 0, 'certXXX')

    with pytest.raises(StructureException):
        # Wrong type for cert data
        actor = PaymentActor('ABCD', 'XYZ', Status.none, [])
        actor.add_kyc_data(kyc, 'sigXXXX', 0)


def test_payment_object_creation():
    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    action = PaymentAction(10, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')

    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)


def test_payment_object_update():
    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    action = PaymentAction(10, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')

    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)
    payment.add_recipient_signature('SIG')

    with pytest.raises(StructureException):
        payment.add_recipient_signature('SIG2')


def test_payment_to_diff():
    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    action = PaymentAction(10, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')

    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)
    record = payment.get_full_diff_record()
    new_payment = PaymentObject.create_from_record(record)
    assert payment == new_payment

    payment2 = PaymentObject(sender, receiver, 'ref2', 'orig_ref', 'desc', action)
    assert payment2 != new_payment


def test_to_json():
    kyc_sender = KYCData("""{
        "payment_reference_id" : "PAYMENT_XYZ",
        "type" : "individual",
        "other_field" : "other data SENDER"
    }""")

    sender = PaymentActor('AAAA', 'aaaa', Status.none, [])
    sender.add_kyc_data(kyc_sender, "sigSENDER", 'certSENDER')

    kyc_receiver = KYCData("""{
        "payment_reference_id" : "PAYMENT_XYZ",
        "type" : "individual",
        "other_field" : "other data RECEIVER"
    }""")

    receiver = PaymentActor('BBBB', 'bbbb', Status.none, [])
    receiver.add_kyc_data(kyc_receiver, "sigSENDER", 'certSENDER')

    action = PaymentAction(1000, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')
    payment = PaymentObject(sender, receiver, 'ref', 'orig_ref', 'desc', action)

    import json
    json_payment = json.dumps(payment.get_full_diff_record())
    new_payment = PaymentObject.create_from_record(json.loads(json_payment))
    assert payment == new_payment


def test_payment_actor_update_bad_kyc_fails(basic_actor):
    diff = {'kyc_data': '1234'}
    with pytest.raises(StructureException):
        basic_actor.custom_update_checks(diff)

    diff = {'kyc_data': '1234', 'kyc_signature': '1234'}
    with pytest.raises(StructureException):
        basic_actor.custom_update_checks(diff)

    diff = {'kyc_certificate': '1234', 'kyc_signature': '1234'}
    with pytest.raises(StructureException):
        basic_actor.custom_update_checks(diff)


def test_payment_actor_update_bad_status_fails(basic_actor):
    diff = {'status': 'wrong_status'}
    with pytest.raises(StructureException):
        basic_actor.custom_update_checks(diff)


def test_payment_actor_update_bad_metadata_fails(basic_actor):
    diff = {'metadata': [1234]}
    with pytest.raises(StructureException):
        basic_actor.custom_update_checks(diff)


def test_payment_actor_add_metadata(basic_actor):
    basic_actor.add_metadata('abcd')
    assert basic_actor.data['metadata'] == ['abcd']