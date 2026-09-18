#!/usr/bin/env python3
"""Local-chain settlement exercise (design note: settlement-local-chain.md).

Deploys the freshly compiled, committed FairSettlement example against a
local Anvil simulation chain and drives the settlement paths with real
transactions — including the deterministic timeouts no wall-clock test could
wait for. Uses Foundry's world-famous dev keys that can never hold value;
nothing here touches a public network. Skips when Foundry or the pinned
compiler is unavailable (CI installs both, pinned).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB = REPO / "scripts" / "solidity-lab.py"
AGREEMENT = REPO / "design" / "solidity-lab" / "settlement-anvil-agreement.json"
SOLC_VERSION = "0.8.37"
LOCAL_PORT = 8547

# Anvil's deterministic dev keys (public knowledge, zero value, local chain only):
# account0 = contributor, account1 = coordinator, account2 = holder.
CONTRIBUTOR_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
COORDINATOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
HOLDER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"

# Backend configuration (see docs/settlement-local-chain.md "one suite, two
# backends"). Defaults are the local simulation chain. A separately authorized
# testnet rehearsal supplies --rpc <url> --attach --key-file <3 hex lines>
# --agreement <json with the matching burner addresses>. The runner never
# reads keys from command-line arguments.
RPC = f"http://127.0.0.1:{LOCAL_PORT}"
KEYS = {"contributor": CONTRIBUTOR_KEY, "coordinator": COORDINATOR_KEY, "holder": HOLDER_KEY}
ATTACH = False
ATTACHED = False  # set when the chain was started by this run

STATE = {"none": 0, "funded": 1, "delivered": 2, "accepted": 3,
         "disputed": 4, "resolved": 5, "released": 6, "refunded": 7}


def _foundry_available() -> bool:
    return bool(shutil.which("anvil")) and bool(shutil.which("cast"))


def _compile_committed_example(out_dir: Path) -> bytes:
    generated = out_dir / "generated"
    result = subprocess.run([sys.executable, str(LAB), "--agreement", str(AGREEMENT),
                             "--out", str(generated)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    compile_result = subprocess.run(
        ["npx", "--yes", f"solc@{SOLC_VERSION}", "--bin", str(generated / "FairSettlement.sol")],
        capture_output=True, text=True, timeout=300, cwd=str(out_dir))
    if compile_result.returncode != 0:
        raise unittest.SkipTest(f"pinned solc unavailable: {compile_result.stderr[:120]}")
    binaries = sorted(out_dir.glob("*_FairSettlement.bin"))
    if not binaries:
        raise unittest.SkipTest(f"pinned solc wrote no binary: {compile_result.stdout[:160]}")
    return bytes.fromhex(binaries[0].read_text().strip())


class AnvilSettlementTests(unittest.TestCase):
    """Every exercised path runs against real EVM execution of the pinned build.

    Each scenario deploys its own fresh instance — the same isolation a
    separately authorized testnet run would use, since real chains offer no
    snapshot/revert."""

    ANVIL = None
    BYTECODE = None
    AGREEMENT_DATA = None

    @classmethod
    def setUpClass(cls) -> None:
        if not _foundry_available():
            raise unittest.SkipTest("foundry (anvil/cast) unavailable in this environment")
        cls.AGREEMENT_DATA = json.loads(AGREEMENT.read_text())
        cls.WORKDIR = tempfile.TemporaryDirectory()
        try:
            cls.BYTECODE = _compile_committed_example(Path(cls.WORKDIR.name))
        except unittest.SkipTest:
            cls.WORKDIR.cleanup()
            raise
        global ATTACHED
        if ATTACH:
            try:
                urllib.request.urlopen(urllib.request.Request(
                    RPC, data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
                    headers={"Content-Type": "application/json"}), timeout=5)
            except Exception as error:
                raise unittest.SkipTest(f"attached chain not reachable: {error}")
            ATTACHED = False  # this run must never stop a chain it did not start
        else:
            cls.ANVIL = subprocess.Popen(["anvil", "--port", str(LOCAL_PORT), "--silent"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ATTACHED = True  # this run owns the chain and stops it in tearDownClass
            ready = False
            for _ in range(30):
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        RPC, data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
                        headers={"Content-Type": "application/json"}), timeout=1)
                    ready = True
                    break
                except Exception:
                    time.sleep(0.5)
            if not ready:
                cls._shutdown()
                raise unittest.SkipTest("local anvil did not become ready")

    @classmethod
    def _shutdown(cls) -> None:
        if not ATTACHED:
            cls.ANVIL = None
        if cls.ANVIL is not None:
            cls.ANVIL.terminate()
            cls.ANVIL = None
        if cls.WORKDIR is not None:
            cls.WORKDIR.cleanup()
            cls.WORKDIR = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls._shutdown()

    def setUp(self) -> None:
        if ATTACH and self._testMethodName in TIME_SCENARIOS:
            raise unittest.SkipTest("time-travel scenarios need the local chain clock")
        deploy = subprocess.run(
            ["cast", "send", "--rpc-url", RPC, "--private-key", KEYS["contributor"],
             "--create", self.BYTECODE.hex(), "--json"],
            capture_output=True, text=True, timeout=120)
        try:
            receipt = json.loads(deploy.stdout)
        except json.JSONDecodeError:
            receipt = {}
        address = receipt.get("contractAddress")
        if deploy.returncode != 0 or receipt.get("status") != "0x1" or not address:
            self.fail(f"deployment failed on local chain: {deploy.stderr[:160]}")
        self.contract = address
        # The chain clock is global and keeps the highest mined timestamp, so
        # every scenario first returns it to the present, below all deadlines.
        self._jump_to(int(time.time()))

    @classmethod
    def _shutdown(cls) -> None:
        if not ATTACHED:
            cls.ANVIL = None
        if cls.ANVIL is not None:
            cls.ANVIL.terminate()
            cls.ANVIL = None
        if cls.WORKDIR is not None:
            cls.WORKDIR.cleanup()
            cls.WORKDIR = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls._shutdown()

    def _cast(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["cast", *args, "--rpc-url", RPC],
                              capture_output=True, text=True, timeout=120)

    def _send(self, key: str, signature: str, *args: str) -> dict:
        result = subprocess.run(["cast", "send", "--rpc-url", RPC, "--private-key", key,
                                 self.contract, signature, *args, "--json"],
                                capture_output=True, text=True, timeout=120)
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError:
            receipt = {}
        receipt["_returncode"] = result.returncode
        receipt["_stderr"] = result.stderr
        return receipt

    def _send_ok(self, key: str, signature: str, *args: str) -> None:
        receipt = self._send(key, signature, *args)
        self.assertTrue(receipt.get("success") is True or receipt.get("status") == "0x1",
                        f"{signature} should succeed: {str(receipt)[:300]}")

    def _send_reverts(self, key: str, signature: str, *args: str, naming: str | None = None) -> None:
        receipt = self._send(key, signature, *args)
        self.assertTrue(receipt.get("success") is False and receipt.get("status") != "0x1",
                        f"{signature} should revert: {str(receipt)[:300]}")
        if naming is not None:
            self.assertIn(naming, str(receipt),
                          f"{signature} should revert naming {naming}")

    def _state(self) -> int:
        result = self._cast("call", self.contract, "state()(uint8)")
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        return int(result.stdout.strip())

    def _jump_to(self, timestamp: int) -> None:
        # evm_setTime also travels backwards; anvil_setNextBlockTimestamp could
        # not, because the chain clock keeps the highest timestamp ever mined.
        result = self._cast("rpc", "evm_setTime", str(timestamp))
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        result = self._cast("rpc", "anvil_mine")
        self.assertEqual(result.returncode, 0, result.stderr[:200])

    def _deliver(self) -> None:
        self._send_ok(KEYS["holder"], "recordFunding()")
        self._send_ok(KEYS["contributor"], "recordDelivery(uint16)", "1")

    def test_deploys_and_starts_in_none(self) -> None:
        self.assertEqual(self._state(), STATE["none"])

    def test_happy_path_releases_on_chain(self) -> None:
        self._send_ok(KEYS["holder"], "recordFunding()")
        self._send_ok(KEYS["contributor"], "recordDelivery(uint16)", "1")
        self.assertEqual(self._state(), STATE["delivered"])
        self._send_ok(KEYS["coordinator"], "accept(uint16)", "1")
        self.assertEqual(self._state(), STATE["accepted"])
        self._send_ok(KEYS["holder"], "release()")
        self.assertEqual(self._state(), STATE["released"])

    def test_roles_hold_on_chain(self) -> None:
        with self.subTest("contributor cannot declare funding"):
            self._send_reverts(KEYS["contributor"], "recordFunding()", naming="NotHolder")
        self._send_ok(KEYS["holder"], "recordFunding()")
        with self.subTest("release before acceptance reverts"):
            self._send_reverts(KEYS["holder"], "release()", naming="WrongState")
        with self.subTest("coordinator cannot deliver"):
            self._send_reverts(KEYS["coordinator"], "recordDelivery(uint16)", "1", naming="NotContributor")
        self._send_ok(KEYS["contributor"], "recordDelivery(uint16)", "1")
        with self.subTest("holder cannot accept"):
            self._send_reverts(KEYS["holder"], "accept(uint16)", "1", naming="NotCoordinator")

    def test_review_deadline_refuses_accept_and_leaves_dispute_path(self) -> None:
        self._deliver()
        self._jump_to(self.AGREEMENT_DATA["review_deadline"] + 1)
        self._send_reverts(KEYS["coordinator"], "accept(uint16)", "1",
                           naming="DeadlinePassed")  # never a silent release
        self._send_ok(KEYS["contributor"], "openDispute(string)", "review deadline passed")
        self.assertEqual(self._state(), STATE["disputed"])

    def test_dispute_fallback_refuses_inside_window_and_refunds_after(self) -> None:
        self._deliver()
        self._send_ok(KEYS["contributor"], "openDispute(string)", "evidence unclear")
        self._jump_to(self.AGREEMENT_DATA["dispute_deadline"])
        self._send_reverts(KEYS["contributor"], "applyDisputeFallback()",
                           naming="DisputeWindowStillOpen")  # window still open
        self._jump_to(self.AGREEMENT_DATA["dispute_deadline"] + 1)
        self._send_ok(KEYS["contributor"], "applyDisputeFallback()")  # permissionless by design
        self.assertEqual(self._state(), STATE["refunded"])

    def test_outer_deadline_refunds_from_delivered(self) -> None:
        self._deliver()
        self._jump_to(self.AGREEMENT_DATA["outer_deadline"] + 1)
        self._send_ok(KEYS["coordinator"], "refundAfterOuterDeadline()")
        self.assertEqual(self._state(), STATE["refunded"])

    def test_cancellation_refunds_before_delivery(self) -> None:
        self._send_ok(KEYS["holder"], "recordFunding()")
        self._send_ok(KEYS["holder"], "refundOnCancellation()")
        self.assertEqual(self._state(), STATE["refunded"])


TIME_SCENARIOS = frozenset({
    "test_review_deadline_refuses_accept_and_leaves_dispute_path",
    "test_dispute_fallback_refuses_inside_window_and_refunds_after",
    "test_outer_deadline_refunds_from_delivered",
})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default=RPC, help="chain endpoint (default: local anvil)")
    parser.add_argument("--attach", action="store_true",
                        help="use --rpc as an already-running chain; never start or stop one")
    parser.add_argument("--key-file", type=Path, default=None,
                        help="file with three hex private keys (contributor, coordinator, holder)")
    parser.add_argument("--agreement", type=Path, default=AGREEMENT,
                        help="settlement agreement JSON (default: the committed anvil agreement)")
    args, remaining = parser.parse_known_args()
    if args.rpc != parser.get_default("rpc") and not args.attach:
        raise SystemExit("--rpc requires --attach (the runner only starts a local chain)")
    RPC = args.rpc
    ATTACH = args.attach
    if args.agreement != AGREEMENT:
        AGREEMENT = args.agreement
    if args.key_file is not None:
        lines = [line.strip() for line in args.key_file.read_text().splitlines() if line.strip()]
        if len(lines) != 3 or any(not line.startswith("0x") or len(line) != 66 for line in lines):
            raise SystemExit("key file must hold exactly three 0x-prefixed 32-byte hex keys")
        KEYS.update(dict(zip(("contributor", "coordinator", "holder"), lines)))
    if not _foundry_available():
        print("foundry (anvil/cast) unavailable — skipping local-chain exercise")
        raise SystemExit(0)
    unittest.main(argv=["test-settlement-anvil", *remaining])
