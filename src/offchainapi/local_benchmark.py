# Sample benchmark to profile performance and observe bottlenecks.
#
# Run as:
# $ python -m cProfile -s tottime src/scripts/run_perf.py > report.txt
#
from .business import BusinessContext, BusinessForceAbort, \
BusinessValidationFailure, VASPInfo
from .protocol import OffChainVASP
from .libra_address import LibraAddress
from .protocol_messages import CommandRequestObject
from .payment_logic import PaymentCommand, PaymentProcessor
from .status_logic import Status
from .storage import StorableFactory
from .payment import PaymentAction, PaymentActor, PaymentObject
from .asyncnet import Aionet

import logging
logging.basicConfig(level=logging.ERROR)

import json
from unittest.mock import MagicMock
from threading import Thread
import time
import asyncio
from aiohttp import web

# A stand alone performance test.

PeerA_addr = LibraAddress.encode_to_Libra_address(b'A'*16)
PeerB_addr = LibraAddress.encode_to_Libra_address(b'B'*16)
peer_address = {
    PeerA_addr.as_str() : 'http://localhost:8091',
    PeerB_addr.as_str() : 'http://localhost:8092',
}

class SimpleVASPInfo(VASPInfo):

    def __init__(self):
        return

    def get_TLS_certificate_path(self):
        raise NotImplementedError()

    def get_TLS_key_path(self):
        raise NotImplementedError()

    def get_peer_TLS_certificate_path(self, other_addr):
        raise NotImplementedError()

    def get_all_peers_TLS_certificate_path(self):
        raise NotImplementedError()

    def get_peer_base_url(self, other_addr):
        assert other_addr.as_str() in peer_address
        return peer_address[other_addr.as_str()]

    def is_authorised_VASP(self, certificate, other_addr):
        return True


class PerfVasp:
    def __init__(self, my_addr, port):
        self.my_addr = my_addr
        self.port = port
        self.bc = MagicMock()
        self.store = StorableFactory({})
        self.info_context = SimpleVASPInfo()
        self.pp = PaymentProcessor(self.bc, self.store)
        self.vasp = OffChainVASP(
            self.my_addr, self.pp, self.store, self.info_context
        )
        self.net_handler = Aionet(self.vasp)

    def start(self, loop):
        # Start the processor
        self.pp.start_processor()

        # Start the server
        runner = self.net_handler.get_runner()
        #
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())

        site = web.TCPSite(runner, 'localhost', self.port)
        loop.run_until_complete(site.start())

        loop.run_forever()

global_dir = {}

def start_thread_main(addr, port, loop):
    node = PerfVasp(addr, port)
    global_dir[addr.as_str()] = node
    node.start(loop)

async def execute(nodeA, nodeB, cmd):
    ret = await nodeA.net_handler.send_command(nodeB.my_addr, cmd)
    return ret

async def main_perf():
    logging.basicConfig(level=logging.DEBUG)

    loopA = asyncio.new_event_loop()
    tA = Thread(target=start_thread_main, args=(PeerA_addr, 8091,loopA), daemon=True)
    tA.start()
    print('Start Node A')

    loopB = asyncio.new_event_loop()
    tB = Thread(target=start_thread_main, args=(PeerB_addr, 8092,loopB), daemon=True)
    tB.start()
    print('Start Node B')

    time.sleep(1.0)
    print('Inject commands')
    time.sleep(1.0)
    while len(global_dir) != 2:
        time.sleep(0.1)
    print(global_dir)

    # Get the channel from A -> B
    nodeA = global_dir[PeerA_addr.as_str()]
    channelAB = nodeA.vasp.get_channel(PeerB_addr)
    nodeB = global_dir[PeerB_addr.as_str()]
    channelBA = nodeB.vasp.get_channel(PeerA_addr)

    # Define a payment command
    commands = []
    for cid in range(100):
        sender = PaymentActor(PeerA_addr.as_str(), 'aaaa', Status.none, [])
        receiver = PaymentActor(PeerB_addr.as_str(), 'bbbb', Status.none, [])
        action = PaymentAction(10, 'TIK', 'charge', '2020-01-02 18:00:00 UTC')
        payment = PaymentObject(
            sender, receiver, f'ref {cid}', 'orig_ref', 'desc', action
        )
        cmd = PaymentCommand(payment)
        commands += [ cmd ]

    s = time.perf_counter()

    async def send100(nodeA, commands):
        res = await asyncio.gather( *[nodeA.net_handler.send_command(nodeB.my_addr, cmd) for cmd in commands] )
        return res

    res = asyncio.run_coroutine_threadsafe(send100(nodeA, commands), loopA)
    res = res.result()

    success_number = sum([1 for r in res if r])
    elapsed = (time.perf_counter() - s)
    print(f'Commands executed in {elapsed:0.2f} seconds.')
    print(f'Success #: {success_number}/{len(commands)}')


    # Esure they were register as successes on both sides.
    Asucc = len([x for x in channelAB.executor.command_status_sequence if x])
    Atotal =   len(channelAB.executor.command_status_sequence)
    print(f'Peer A successes: {Asucc}/{Atotal}')

    Bsucc = len([x for x in channelBA.executor.command_status_sequence if x])
    Btotal =   len(channelBA.executor.command_status_sequence)
    print(f'Peer B successes: {Bsucc}/{Btotal}')

    print(f'Estimate throughput #: {len(commands)/elapsed} Tx/s')

    import sys
    sys.exit()