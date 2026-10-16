"""Deterministic public-interface traffic, including invalid controls and contention."""
import math
import random
import struct
from model import INPUTS, OUTPUTS, FIXTURES, Protocol, pack


class Scenario:
    def __init__(self, folder, config, seed=11):
        self.folder, self.config, self.seed = folder, config, seed
        self.p = Protocol(*config)
        self.rng = random.Random(seed)
        self.vectors = (folder/"vectors.hex").open("w")
        self.expected = (folder/"expected.bin").open("wb")
        self.cycles = 0
        self.stall_runs = {}
        self.current_stall = 0
        self.inputs = []

    def cycle(self, **request):
        v = {name: 0 for name, _ in INPUTS}
        v.update(rst_n=1, m_ready=1)
        v.update(request)
        v["cfg_addr"] &= (1 << max(1, (self.config[0]-1).bit_length()))-1
        pre, post = self.p.step(v)
        retired = bool(pre["m_valid"] and v["m_ready"] and v["rst_n"])
        self.vectors.write(f"{pack(INPUTS, v):x}\n")
        self.expected.write(struct.pack("<IIB", pack(OUTPUTS, pre),
                                        pack(OUTPUTS, post), retired))
        self.cycles += 1
        if pre["m_valid"] and not v["m_ready"]:
            self.current_stall += 1
            self.stall_runs[self.p.mode] = max(
                self.current_stall, self.stall_runs.get(self.p.mode, 0))
        else:
            self.current_stall = 0
        return pre, post

    def drain(self):
        while self.p.pending:
            self.cycle()

    def reset(self):
        self.cycle(rst_n=0)
        self.cycle(rst_n=0, s_valid=1, cfg_valid=1, sos_cfg_valid=1,
                   chain_valid=1, sos_commit_valid=1, commit_valid=1)
        self.cycle()

    def program(self, fir=None, sos=None, channels=range(4), commit=True):
        taps, _, count = self.config
        fir = fir if fir is not None else fir_fixture(taps, "asymmetric")
        sos = sos if sos is not None else [FIXTURES["LP50"]]*count
        for c in channels:
            for i in range(max(taps, count*5)):
                req = {}
                if i < taps:
                    req.update(cfg_valid=1, cfg_channel=c, cfg_addr=i, cfg_data=fir[i])
                if i < count*5:
                    req.update(sos_cfg_valid=1, sos_cfg_channel=c,
                               sos_cfg_section=i//5, sos_cfg_addr=i % 5,
                               sos_cfg_data=sos[i//5][i % 5])
                pre, _ = self.cycle(**req)
                if i < taps:
                    assert pre["cfg_ready"]
                if i < count*5:
                    assert pre["sos_cfg_ready"]
            if commit:
                self.drain()
                pre, _ = self.cycle(commit_valid=1, commit_channel=c)
                assert pre["commit_ready"]
                pre, _ = self.cycle(sos_commit_valid=1, sos_commit_channel=c)
                assert pre["sos_commit_ready"]

    def chain(self, mode, count):
        self.drain()
        pre, _ = self.cycle(chain_valid=1, chain_mode=mode, chain_sections=count)
        assert pre["chain_ready"]

    def samples(self, values, stalls=False, hold_first=False, collect=False):
        """values is iterable of (channel, signed code), held until handshake."""
        iterator = iter(values)
        item = next(iterator, None)
        held = 0
        holding_done = not hold_first
        while item is not None or self.p.pending:
            c, x = item if item is not None else (0, 0)
            mr = int(not stalls or self.rng.randrange(5) != 0)
            if not holding_done:
                mr = 0
                if self.p.visible():
                    held += 1
                    if held == 32:
                        holding_done = True
            sv = int(item is not None and (not stalls or self.rng.randrange(7) != 0))
            # Once presented, the payload is retained until accepted.
            if getattr(self, "_blocked_valid", False):
                sv = 1
            pre, _ = self.cycle(s_valid=sv, s_data=x, s_channel=c, m_ready=mr)
            self._blocked_valid = bool(sv and not pre["s_ready"])
            if sv and pre["s_ready"]:
                if collect:
                    self.inputs.append((c, x))
                item = next(iterator, None)
        assert not self._blocked_valid

    def close(self):
        self.vectors.close()
        self.expected.close()
        p = self.p
        return dict(config=self.config, seed=self.seed, cycles=self.cycles,
                    accepted=p.accepted, retired=p.retired, aborted=p.aborted,
                    outstanding=int(p.pending is not None),
                    mode_channel_counts=dict(p.mode_counts),
                    expected_edge_latencies=p.latencies,
                    minimum_accept_intervals=p.intervals,
                    longest_output_stalls=self.stall_runs,
                    coverage=dict(p.coverage))


def fir_fixture(taps, kind="asymmetric"):
    if kind == "zero":
        return [0]*taps
    if kind == "single":
        return [-16384]+[0]*(taps-1)
    if kind == "average":
        return [32768//taps]*taps if taps > 1 else [32767]
    return [((i*7919+733) % 8192)-4096 for i in range(taps)]


def stream(n, seed, bound=8192):
    rng = random.Random(seed)
    for i in range(n):
        # Consecutive same-channel pairs plus round-robin isolation.
        for c in ((i//2) % 4, (i//2+1) % 4, (i//2+2) % 4, (i//2+3) % 4):
            k = i % 64
            if k == 0:
                x = bound
            elif k < 8:
                x = 0
            elif k < 16:
                x = bound
            elif k < 24:
                x = -bound
            elif k < 40:
                x = round(bound*math.sin(2*math.pi*(.03125 if c % 2 else .46875)*i))
            else:
                x = rng.randint(-bound, bound)
            yield c, x


def matrix(s, n=64, extended=False):
    s.reset()
    s.program()
    for mode in range(5):
        counts = [0] if mode < 2 else ([s.config[2]] if extended else
                                      range(1, s.config[2]+1))
        for count in counts:
            s.chain(mode, count)
            s.samples(stream(n, s.seed+mode*17+count), stalls=True, hold_first=True)
            s.samples(((c, x) for c, x in [(0, 123), (0, -321)]*8))
    if not extended:
        assert all(s.stall_runs.get(m, 0) >= 32 for m in range(5))


def compatibility(s):
    s.reset()
    # Only legacy pins are driven so the separately instantiated parent is comparable.
    for kind in ("zero", "single", "average", "asymmetric"):
        fir = fir_fixture(s.config[0], kind)
        for c in range(4):
            for i, coeff in enumerate(fir):
                s.cycle(cfg_valid=1, cfg_channel=c, cfg_addr=i, cfg_data=coeff)
            s.cycle(commit_valid=1, commit_channel=c)
        s.samples(stream(64, s.seed, 32767), stalls=True, hold_first=True)
        s.samples([(0, 1), (0, 3), (1, -1), (1, -3)]*8)
    # Invalid legacy address exists on the one-tap port only.
    if s.config[0] == 1:
        s.cycle(cfg_valid=1, cfg_addr=1)
    s.cycle(commit_valid=1, s_valid=1)
    s.reset()


def directed(s):
    s.reset()
    pre, _ = s.cycle(commit_valid=1, s_valid=1)
    assert not pre["commit_ready"] and not pre["s_ready"]
    # Incomplete commits, duplicate writes, address validity and higher-invalid priority.
    s.cycle(cfg_valid=1, cfg_addr=0, cfg_data=17,
            sos_cfg_valid=1, sos_cfg_data=33)
    s.cycle(cfg_valid=1, cfg_addr=0, cfg_data=19,
            sos_cfg_valid=1, sos_cfg_data=35)
    for req in (dict(sos_commit_valid=1), dict(commit_valid=1),
                dict(chain_valid=1, chain_mode=7, chain_sections=7,
                     sos_commit_valid=1, commit_valid=1),
                dict(sos_commit_valid=1, commit_valid=1)):
        s.cycle(s_valid=1, cfg_valid=1, sos_cfg_valid=1, **req)
    for addr in (5, 6, 7):
        s.cycle(sos_cfg_valid=1, sos_cfg_addr=addr, s_valid=1)
        s.drain()
    if s.config[2] < 4:
        s.cycle(sos_cfg_valid=1, sos_cfg_section=s.config[2])
    if s.config[0] == 1:
        s.cycle(cfg_valid=1, cfg_addr=1)
    s.program()
    for mode in range(8):
        for count in range(8):
            legal = (mode < 2 and count == 0) or (
                mode in (2, 3, 4) and 1 <= count <= s.config[2])
            if not legal:
                pre, _ = s.cycle(chain_valid=1, chain_mode=mode,
                                 chain_sections=count, s_valid=1,
                                 cfg_valid=1, sos_cfg_valid=1, commit_valid=1,
                                 sos_commit_valid=1)
                assert not any(pre[k] for k in ("chain_ready", "s_ready",
                                                "cfg_ready", "sos_cfg_ready",
                                                "commit_ready", "sos_commit_ready"))
    # Fill both banks, verify all priority pairs with complete requests.
    for toggle in range(2):
        s.program(commit=False)
        for req in (dict(chain_valid=1, chain_mode=1, sos_commit_valid=1),
                    dict(chain_valid=1, chain_mode=1, commit_valid=1)):
            s.cycle(s_valid=1, cfg_valid=1, sos_cfg_valid=1, **req)
        pre, _ = s.cycle(sos_commit_valid=1, commit_valid=1)
        assert pre["sos_commit_ready"] and not pre["commit_ready"]
        s.cycle(commit_valid=1)
        for c in (1, 2, 3):
            s.cycle(sos_commit_valid=1, sos_commit_channel=c)
            s.cycle(commit_valid=1, commit_channel=c)
        for mode in range(5):
            s.chain(mode, 0 if mode < 2 else s.config[2])
            s.samples(stream(16, 29+toggle), hold_first=True)
            # Accept a sample, then write both inactive banks while busy/holding output.
            pre, _ = s.cycle(s_valid=1, s_data=12345, s_channel=2, m_ready=0)
            assert pre["s_ready"]
            for i in range(4):
                s.cycle(cfg_valid=1, cfg_channel=2, cfg_data=100+i,
                        sos_cfg_valid=1, sos_cfg_channel=2, sos_cfg_data=200+i,
                        m_ready=0)
            # Pending output must block every update, even on retirement.
            while not s.p.visible():
                s.cycle(m_ready=0)
            for req in (dict(chain_valid=1, chain_mode=0),
                        dict(sos_commit_valid=1), dict(commit_valid=1)):
                pre, _ = s.cycle(m_ready=0, **req)
                assert not pre["chain_ready"] and not pre["sos_commit_ready"] \
                    and not pre["commit_ready"]
            s.drain()
            s.samples([(0, i-12) for i in range(24)])
    # FIR history survives coefficient changes and periods of non-FIR traversal.
    s.chain(1, 0)
    s.samples([(0, 12000), (1, -9000)])
    s.chain(0, 0)
    s.samples([(0, 30000)]*4)
    s.program(fir=fir_fixture(s.config[0], "average"))
    s.chain(4, s.config[2])
    s.samples(stream(64, 97))
    # A channel-local commit must clear its nonzero recursion without clearing peers.
    s.chain(2, s.config[2])
    s.samples([(c, 8192-c*1024) for _ in range(8) for c in range(4)])
    s.program(sos=[FIXTURES["HP50"]]*s.config[2], channels=(0,), commit=False)
    pre, _ = s.cycle(sos_commit_valid=1, sos_commit_channel=0)
    assert pre["sos_commit_ready"]
    s.samples([(c, 0) for _ in range(16) for c in range(4)])
    # Distinct banks/coefficients expose coefficient as well as history isolation.
    for c in range(4):
        coeff = [(-1 if c & 1 else 1)*(4096+c*1024)]+[0]*(s.config[0]-1)
        fixture = FIXTURES[("LP50", "HP50", "LP75", "LP50")[c]]
        s.program(fir=coeff, sos=[fixture]*s.config[2], channels=(c,))
    for mode in (1, 2, 3, 4):
        s.chain(mode, 0 if mode == 1 else s.config[2])
        s.samples(stream(16, 11))
    s.drain()
    pre, _ = s.cycle(s_valid=1, s_data=2345, s_channel=3,
                     cfg_valid=1, cfg_channel=3, cfg_addr=0, cfg_data=-1234,
                     sos_cfg_valid=1, sos_cfg_channel=3, sos_cfg_data=12345)
    assert pre["s_ready"] and pre["cfg_ready"] and pre["sos_cfg_ready"]
    s.drain()
    # Both asynchronous reset-abort classes; fields change while clock is low.
    for held in (False, True):
        s.chain(2, s.config[2])
        s.cycle(s_valid=1, s_data=12000, m_ready=0)
        if held:
            while not s.p.visible():
                s.cycle(m_ready=0)
        s.reset()
        s.samples(stream(8, 11))
    # Even a one-section active prefix requires the entire physical bank.
    s.program()
    s.chain(2, 1)
    s.samples([(c, 8192) for _ in range(8) for c in range(4)])
    missing = (s.config[2]-1, 4)
    for section in range(s.config[2]):
        for address, value in enumerate(FIXTURES["HP50"]):
            if (section, address) != missing:
                s.cycle(sos_cfg_valid=1, sos_cfg_section=section,
                        sos_cfg_addr=address, sos_cfg_data=value)
    pre, _ = s.cycle(sos_commit_valid=1, s_valid=1)
    assert not pre["sos_commit_ready"] and not pre["s_ready"]
    s.cycle(sos_cfg_valid=1, sos_cfg_data=FIXTURES["HP50"][0])
    pre, _ = s.cycle(sos_commit_valid=1)
    assert not pre["sos_commit_ready"]
    s.cycle(sos_cfg_valid=1, sos_cfg_section=missing[0],
            sos_cfg_addr=missing[1], sos_cfg_data=FIXTURES["HP50"][4])
    pre, _ = s.cycle(sos_commit_valid=1)
    assert pre["sos_commit_ready"]
    s.samples([(c, 0) for _ in range(16) for c in range(4)])


def arithmetic(s):
    s.reset()
    # Reach all signed half-way parity combinations at each quantization boundary.
    for b0 in (128, 32768):
        s.program(fir=[16384]+[0]*(s.config[0]-1),
                  sos=[(b0, 0, 0, 0, 0)]*s.config[2])
        for mode in (1, 2, 3, 4):
            s.chain(mode, int(mode > 1))
            s.samples([(c, x) for x in (1, 3, -1, -3, 32767, -32768)
                       for c in range(4)])
    # Intentional unstable programming is arithmetic stress, never stability evidence.
    for sign in (1, -1):
        s.program(fir=[32767]*s.config[0],
                  sos=[(131071, 131071, 131071, -131072, -131072)]*s.config[2])
        for mode in (1, 2, 3, 4):
            s.chain(mode, s.config[2] if mode > 1 else 0)
            s.samples([(c, sign*32767) for _ in range(80) for c in range(4)])
    # Cancellation in FIR and SOS with nonzero individual products.
    s.program(fir=[16384, -16384]+[0]*(s.config[0]-2) if s.config[0] > 1
              else [0], sos=[(65536, -65536, 0, 0, 0)]*s.config[2])
    for mode in (1, 2):
        s.chain(mode, int(mode == 2))
        s.samples([(c, 4096) for _ in range(12) for c in range(4)])
    s.program(fir=[-32768]+[0]*(s.config[0]-1),
              sos=[(65536, 0, 0, 0, 0)]*s.config[2])
    for mode in (0, 1, 2):
        s.chain(mode, int(mode == 2))
        s.samples([(c, x) for x in (-32768, -32767, 32767, 0) for c in range(4)])


def defect_workload(s, kind):
    s.reset()
    fir = [32767]*s.config[0]
    sos = [FIXTURES["LP50"]]*s.config[2]
    s.program(fir=fir, sos=sos)
    s.chain(3 if kind == "chain_order" else 2, s.config[2])
    s.samples(stream(128, 29, 32767))
    s.program(fir=fir_fixture(s.config[0], "asymmetric"),
              sos=[FIXTURES["HP50"]]*s.config[2])
    s.chain(4 if kind == "chain_order" else 2, s.config[2])
    s.samples(stream(128, 97, 32767))
