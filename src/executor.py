''' These interfaces are heavily WIP, as we decide how to best implement the
    above state machines '''

from os import urandom
from base64 import standard_b64encode
from copy import deepcopy
def get_unique_string():
    return standard_b64encode(urandom(16))

# Generic interface to a shared object

class SharedObject:
    def __init__(self):
        ''' All objects have a version number and their commit status '''
        self.version = get_unique_string()
        self.extends = [] # Strores the version of the previous object

        # Flags indicate the state of the object in the store
        self.potentially_live = False   # Pending commands could make it live
        self.actually_live = False   # Successful command made it live

    def new_version(self):
        ''' Make a deep copy of an object with a new version number '''
        clone = deepcopy(self)
        clone.extends = [ self.get_version() ]
        clone.version = get_unique_string()
        return clone

    def get_version(self):
        ''' Return a unique version number to this object and version '''
        return self.version

    def get_potentially_live(self):
        return self.potentially_live

    def set_potentially_live(self, flag):
        self.potentially_live = flag

    def get_actually_live(self):
        return self.actually_live

    def set_actually_live(self, flag):
        self.actually_live = flag


# Interface we need to do commands:
class ProtocolCommand:
    def __init__(self, command):
        self.depend_on = []
        self.creates   = []
        self.command   = command
        self.commit_status = None

    def get_dependencies(self):
        return set(self.depend_on)

    def new_object_versions(self):
        return set(self.creates)

    def validity_checks(self, dependencies, maybe_own=True):
        return True

    def get_object(self, version_number, dependencies):
        assert version_number in self.new_object_versions()
        raise NotImplementedError('You need to subclass and override this method')

    def on_success(self):
        pass

    def on_fail(self):
        pass

class ProtocolExecutor:
    def __init__(self):
        self.seq = []
        self.last_confirmed = 0

        # This is the primary store of shared objects.
        # It maps version numbers -> objects
        self.object_store = { } # TODO: persist this structure

    def next_seq(self):
        return len(self.seq)

    def count_potentially_live(self):
        return sum(1 for obj in self.object_store.values() if obj.get_potentially_live())

    def count_actually_live(self):
        return sum(1 for obj in self.object_store.values() if obj.get_actually_live())

    def all_true(self, versions, predicate):
        for version in versions:
            if version not in self.object_store:
                return False
            obj = self.object_store[version]
            res = predicate(obj)
            if not res:
                return False
        return True

    def sequence_next_command(self, command, do_not_sequence_errors = False, own=True):
        dependencies = command.get_dependencies()

        if own:
            # For our own commands we do speculative execution
            predicate = lambda obj: obj.get_potentially_live()
        else:
            # For the other commands we do actual execution
            predicate = lambda obj: obj.get_actually_live()

        all_good = self.all_true(dependencies, predicate)

        # TODO: Here we need to pass the business logic.
        all_good &= command.validity_checks(self.object_store, own)

        if not all_good and do_not_sequence_errors:
            # TODO: Define proper exception
            raise ExecutorCannotSequence('Cannot sequence')

        pos = len(self.seq)
        self.seq += [ command ]

        if not all_good:
            # TODO: Define proper exception
            raise ExecutorCannotSequence('Invalid ... ')

        if all_good:
            new_versions = command.new_object_versions()
            for version in new_versions:
                obj = command.get_object(version, self.object_store)
                obj.set_potentially_live(True)
                self.object_store[version] = obj

        return pos

    def set_success(self, seq_no):
        # print('start success', self.last_confirmed, seq_no, len(self.seq))
        assert seq_no == self.last_confirmed
        self.last_confirmed += 1

        command = self.seq[seq_no]
        command.commit_status = True
        # Consumes old objects
        dependencies = command.get_dependencies()
        for version in dependencies:
            del self.object_store[version]

        # Creates new objects
        new_versions = command.new_object_versions()
        # print('success', seq_no, new_versions)
        for version in new_versions:
            obj = self.object_store[version]
            obj.set_actually_live(True)

        command.on_success()

    def set_fail(self, seq_no):
        # print('start fail', self.last_confirmed, seq_no, len(self.seq))
        assert seq_no == self.last_confirmed
        self.last_confirmed += 1

        command = self.seq[seq_no]
        command.commit_status = False

        new_versions = command.new_object_versions()
        for version in new_versions:
            if version in self.object_store:
                del self.object_store[version]

        command.on_fail()

class ExecutorCannotSequence(Exception):
    pass
