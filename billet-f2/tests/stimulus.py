"""Deterministic protocol boundaries followed by seeded interleaved traffic."""

import random

from model import FIELDS, Oracle
from support import require


class Stimulus:
    def __init__(self, taps, lanes, seed, compatibility=False):
        self.taps = taps
        self.channels = [0] if compatibility else list(range(4))
        self.random = random.Random(seed)
        self.model = Oracle(taps, lanes)
        self.drive = dict(zip(FIELDS, [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]))
        self.rows = []
        self.phases = []
        self.phase = "initial"

    def step(self, **updates):
        self.drive.update(updates)
        row = self.drive.copy()
        pre, post = self.model.tick(row, self.phase)
        self.rows.append(row)
        self.phases.append(self.phase)
        return pre, post

    def reset(self):
        self.step(rst_n=0, s_valid=0, cfg_valid=0, commit_valid=0, m_ready=0)
        self.step()
        self.step(rst_n=1, m_ready=1)
        for ch in self.channels:
            self.send(ch, 1234 + ch)

    def drain(self):
        self.drive.update(s_valid=0, cfg_valid=0, commit_valid=0, m_ready=1)
        for _ in range(self.model.groups + 3):
            self.step()
            if self.model.pending is None:
                return
        raise AssertionError("cycle-bounded drain failed")

    def send(self, channel, value, wait=True, **updates):
        self.drive.update(updates)
        for _ in range(self.model.groups + 4):
            pre, _ = self.step(s_valid=1, s_channel=channel, s_data=value)
            if pre[0]:
                if wait:
                    self.drain()
                return
        raise AssertionError("cycle-bounded sample acceptance failed")

    def load(self, channel, coefficients):
        self.drain()
        # Final missing address is written last; probe each channel's independent
        # completeness while idle, with commit_valid low.
        for addr in range(self.taps):
            self.step(cfg_valid=1, cfg_channel=channel, cfg_addr=addr,
                      cfg_data=(coefficients[addr] ^ 1) if addr == 0 else coefficients[addr],
                      commit_channel=channel)
            if addr == 0:
                self.step(cfg_data=coefficients[addr])
        self.step(cfg_valid=0)
        for ch in self.channels:
            self.step(commit_channel=ch)

    def swap(self, channel):
        pre, _ = self.step(commit_valid=1, commit_channel=channel)
        require(pre[2] == 1, "stimulus attempted incomplete swap")
        self.step(commit_valid=0)

    def program(self, coefficient_function):
        for ch in self.channels:
            self.load(ch, coefficient_function(ch))
            self.swap(ch)
            self.send(ch, 0)

    def incomplete(self, channel, reset=False):
        self.drain()
        # For TAP_COUNT=1, no accepted write is an incomplete generation.
        for addr in range(self.taps - 1):
            self.step(cfg_valid=1, cfg_channel=channel, cfg_addr=addr, cfg_data=13)
            self.step()
        self.step(cfg_valid=0)
        for _ in range(3):
            pre, _ = self.step(commit_valid=1, commit_channel=channel,
                               cfg_valid=1, cfg_channel=channel,
                               cfg_addr=self.taps - 1, cfg_data=19,
                               s_valid=1, s_channel=channel, s_data=7)
            require(pre[:3] == [0, 0, 0], "incomplete request did not block producers")
        if reset:
            self.reset()
        else:
            # Explicit withdrawal of an unaccepted incomplete commit is legal;
            # the suppressed sample/configuration producers remain held.
            self.step(commit_valid=0)
            self.drain()

    def run(self):
        self.reset()
        for ch in self.channels:
            self.send(ch, 0)
            self.send(ch, 12345)
        if self.taps == 1:
            self.step(cfg_valid=1, cfg_addr=1, cfg_data=22)
            self.step()
            self.reset()
        self.phase = "incomplete"
        for ch in self.channels:
            self.incomplete(ch, reset=(ch == self.channels[-1]))

        self.phase = "reset_compute"
        self.program(lambda c: [1200 + 17 * c + k for k in range(self.taps)])
        self.send(self.channels[0], 23456, wait=False, m_ready=0)
        self.reset()  # Genuine falling reset between E0 and E1, even for G=1.

        self.phase = "reset_stalled"
        self.program(lambda c: [2100 + 19 * c + k for k in range(self.taps)])
        self.send(self.channels[-1], -23123, wait=False, m_ready=0)
        self.step(s_valid=0)
        for _ in range(self.model.groups + 2):
            self.step()
        require(self.model.pins(self.drive)[3] == 1, "reset-stall setup")
        self.reset()

        self.phase = "reset_blocked_commit"
        self.incomplete(self.channels[0], reset=True)
        self.phase = "refill_after_final_reset"
        self.program(lambda c: [3000 + 23 * c + k for k in range(self.taps)])

        self.phase = "ties"
        self.program(lambda c: [16384] + [0] * (self.taps - 1))
        for ch in self.channels:
            for value in (0, 1, 3, -1, -3, 32767, -32768):
                self.send(ch, value)
        for coefficient in (16383, 16385):
            self.phase = "near_half"
            self.program(lambda c: [coefficient] + [0] * (self.taps - 1))
            for ch in self.channels:
                for value in (1, -1):
                    self.send(ch, value)

        self.phase = "impulse_and_independent_history"
        self.program(lambda c: [(-1 if k % 2 else 1) * (500 + 29 * k + c * 73)
                                for k in range(self.taps)])
        for _ in range(self.taps):
            for ch in self.channels:
                self.send(ch, 0)
        for value in (32767, -32768):
            for ch in self.channels:
                self.send(ch, value)
            for _ in range(self.taps):
                for ch in self.channels:
                    self.send(ch, 0)
        for n in range(self.taps + 2):
            for ch in self.channels:
                self.send(ch, 32767 if (n + ch) % 2 else -32768)

        self.phase = "positive_saturation"
        self.program(lambda c: ([-32768] if self.taps == 1 else [32767] * self.taps))
        for _ in range(self.taps + 2):
            for ch in self.channels:
                self.send(ch, -32768 if self.taps == 1 else 32767)
        self.phase = "negative_saturation"
        self.program(lambda c: [-32768] * self.taps)
        for _ in range(self.taps + 2):
            for ch in self.channels:
                self.send(ch, 32767)
        self.phase = "negative_times_negative_full_scale"
        for _ in range(self.taps + 2):
            for ch in self.channels:
                self.send(ch, -32768)
        self.phase = "lane_reduction_cancellation"
        self.program(lambda c: [32767 if k % 2 == 0 else -32768
                                for k in range(self.taps)])
        for _ in range(self.taps + 2):
            for ch in self.channels:
                self.send(ch, 32767)

        self.phase = "frozen_bank_ownership"
        for other in (False, True) if len(self.channels) > 1 else (False,):
            for ch in self.channels:
                target = (ch + 1) % 4 if other else ch
                coefficients = [(-1700 if other else 2300) + k * 7 + target * 31
                                for k in range(self.taps)]
                self.load(target, coefficients)
                self.send(ch, 18000 + ch, wait=False, m_ready=0,
                          cfg_valid=1, cfg_channel=target, cfg_addr=0,
                          cfg_data=coefficients[0], commit_channel=target)
                # A write on a computing edge and on a stalled-output edge.
                self.step(s_valid=0)
                while not self.model.pins(self.drive)[3]:
                    self.step()
                for _ in range(3):
                    self.step()
                # Stall a complete commit through output retirement. A second
                # computing transaction tests a commit requested while busy.
                self.drain()
                self.send(ch, -18000 - ch, wait=False, m_ready=0,
                          cfg_valid=0, commit_channel=target)
                for _ in range(self.model.groups + 22):
                    self.step(commit_valid=1, commit_channel=target,
                              cfg_valid=1, cfg_channel=target, cfg_addr=0,
                              cfg_data=coefficients[0],
                              s_valid=1, s_channel=target, s_data=12347)
                pre, _ = self.step(m_ready=1)
                require(pre[3] == 1 and pre[2] == 0, "retire/commit edge exclusion")
                pre, _ = self.step()
                require(pre[2] == 1, "commit did not become ready after retirement")
                pre, _ = self.step(commit_valid=0)
                require(pre[0] == 1 and pre[1] == 1, "first new-bank sample/write overlap")
                self.drain()

        self.phase = "measure"
        deck = [ch for ch in self.channels for _ in range(280)]
        self.random.shuffle(deck)
        self.burst(deck, backpressure=False)
        self.phase = "randomized_backpressure"
        deck = [ch for ch in self.channels for _ in range(48)]
        self.random.shuffle(deck)
        self.burst(deck, backpressure=True)
        self.drain()
        for _ in range(self.model.groups + 3):
            self.step()
        return self.rows, self.phases

    def burst(self, deck, backpressure):
        self.drive.update(cfg_valid=0, commit_valid=0, m_ready=1)
        for ch in deck:
            value = self.random.randint(-32768, 32767)
            for _ in range((self.model.groups + 1) * 64):
                kwargs = {}
                if backpressure:
                    kwargs = {
                        "m_ready": int(self.random.random() >= 0.45),
                        "cfg_valid": int(self.random.random() < 0.3),
                        "cfg_channel": self.random.choice(self.channels),
                        "cfg_addr": self.random.randrange(self.taps),
                        "cfg_data": self.random.randint(-32768, 32767),
                    }
                    # cfg never stalls in this phase, so each value is a new
                    # accepted write. Sample payload is held until acceptance.
                pre, _ = self.step(s_valid=1, s_channel=ch, s_data=value, **kwargs)
                if pre[0]:
                    break
            else:
                raise AssertionError("cycle-bounded random acceptance failed")
        self.drain()
