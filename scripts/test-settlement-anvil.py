#!/usr/bin/env python3
"""Local-chain settlement exercise (design note: settlement-local-chain.md).

Deploys the freshly compiled, committed FairSettlement example against a
local Anvil simulation chain and drives the settlement paths with real
transactions — including the deterministic timeouts no wall-clock test could
wait for. Nothing here touches a public network. Missing Foundry or an
unavailable pinned compiler skip explicitly outside CI; under CI both are
mandatory gates and fail hard. A compiler that rejects the generated source,
or writes no artifacts, always fails everywhere (review A3) — never a green
skip.

The runner owns its target and its signers (review A6):

- The default run PROVES the port serves its own anvil process: the port
  must answer nothing beforehand, the process is started by this run, and
  the chain id is read back and COMPARED — a foreign responding instance is
  never this suite's test server.
- An attached run must name its endpoint, its expected chain id (compared,
  never inferred from an exit code) and dedicated keystores. Anvil's public
  dev accounts may sign only on the chain this run owns; attached runs
  reject them, and the agreement's roles must bind to the addresses that
  actually sign.
- Private key material never travels in process arguments: the local run
  signs through its own anvil's unlocked dev accounts (the keys stay inside
  the chain process), attached runs sign from encrypted keystores with the
  password read from a file.
- Processes are stopped only if this run started them — terminate, wait,
  and only then kill.
"""

import argparse
import errno
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB = REPO / "scripts" / "solidity-lab.py"
AGREEMENT = REPO / "design" / "solidity-lab" / "settlement-anvil-agreement.json"
LOCAL_PORT = 8547
LOCAL_CHAIN_ID = 31337  # anvil's dev chain id; this run pins its own instance to it

# The only chains an attached run may target (review A6): a private local
# anvil instance and the documented Sepolia rehearsal. Anything else —
# mainnet in particular — needs an explicit new design decision first.
ALLOWED_ATTACH_CHAIN_IDS = frozenset({LOCAL_CHAIN_ID, 11155111})

# Anvil's deterministic dev accounts: contributor, coordinator, holder
# (account0..2). The ADDRESSES are public knowledge; the matching keys are
# public too and can never hold value — this runner never touches key bytes
# at all, it asks its own anvil to sign for these unlocked accounts.
DEV_ACCOUNTS = {
    "contributor": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
    "coordinator": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    "holder": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
}
ROLES = ("contributor", "coordinator", "holder")

STATE = {"none": 0, "funded": 1, "delivered": 2, "accepted": 3,
         "disputed": 4, "resolved": 5, "released": 6, "refunded": 7}


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "design" / "solidity-lab" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_SOLC = _load_module("pinned_solc")


class RunnerError(Exception):
    """The runner's own boundary was violated (review A6)."""


def _foundry_available() -> bool:
    return bool(shutil.which("anvil")) and bool(shutil.which("cast"))


# ---- Chain identity and ownership (review A6) ----

def _rpc_call(rpc: str, method: str, params: list, timeout: float = 15):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(rpc, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    try:
        answer = json.loads(body)
    except json.JSONDecodeError as error:
        raise RunnerError(f"{rpc} did not answer JSON-RPC for {method}") from error
    if answer.get("error") is not None:
        raise RunnerError(f"{method} failed on {rpc}: {answer['error']}")
    return answer.get("result")


def chain_id_at(rpc: str) -> int:
    return int(_rpc_call(rpc, "eth_chainId", []), 16)


def require_chain_id(rpc: str, expected: int) -> None:
    """The chain id is COMPARED, never inferred from an exit code (review A6)."""
    actual = chain_id_at(rpc)
    if actual != expected:
        raise RunnerError(
            f"the chain at {rpc} identifies as {actual}, expected {expected}; "
            "refusing to run against the wrong chain")


def require_port_free(rpc: str) -> None:
    """Ownership proof, part one: before this run starts its own anvil, the
    port must answer NOTHING. Any responding instance — RPC, plain HTTP,
    anything — is foreign; it is never this suite's test server."""
    request = urllib.request.Request(
        rpc, data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            response.read(1)
    except urllib.error.URLError as error:
        reason = error.reason
        if isinstance(reason, ConnectionRefusedError) or (
                isinstance(reason, OSError) and reason.errno in (errno.ECONNREFUSED, errno.ECONNRESET)):
            return  # nothing listens there: free for this run's own process
        raise RunnerError(
            f"{rpc} is neither free nor a usable chain endpoint ({reason}); "
            "a foreign responding instance is not this suite's test server") from error
    except OSError as error:
        raise RunnerError(
            f"{rpc} is neither free nor a usable chain endpoint ({error}); "
            "a foreign responding instance is not this suite's test server") from error
    raise RunnerError(
        f"{rpc} already answers; a foreign responding instance is not this "
        "suite's test server — stop it or free the port before running")


def start_own_anvil(port: int, chain_id: int):
    """Ownership proof, part two: this run starts the process itself, pinned
    to the expected chain id, and proves it is alive once the RPC answers."""
    process = subprocess.Popen(
        ["anvil", "--port", str(port), "--chain-id", str(chain_id), "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rpc = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if process.poll() is not None:
            raise RunnerError(f"own anvil exited during startup with code {process.returncode}")
        try:
            chain_id_at(rpc)
        except RunnerError:
            raise
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
            continue
        if process.poll() is not None:
            raise RunnerError(f"own anvil exited right after becoming ready (code {process.returncode})")
        return process
    raise RunnerError("own anvil did not become ready within 30 seconds")


def stop_own_chain(process) -> None:
    """Stop only processes this run started — and always WAIT for them
    (review A6: the old cleanup left the child un-waited, ResourceWarning)."""
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _free_private_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---- Signer identity (review A6) ----

def read_keystore_addresses(keystore_dir: Path, password_file: Path) -> dict:
    """Derive the actual signer addresses with cast from the keystores — no
    key material ever appears on a command line."""
    addresses = {}
    for role in ROLES:
        result = subprocess.run(
            ["cast", "wallet", "address", "--keystore", str(Path(keystore_dir) / role),
             "--password-file", str(password_file)],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not result.stdout.strip().startswith("0x"):
            raise RunnerError(
                f"cannot derive the {role} signer address from its keystore: "
                f"{result.stderr.strip()[:160]}")
        addresses[role] = result.stdout.strip()
    return addresses


def reject_dev_account_signers(signers: dict) -> None:
    """An attached run must never sign with Anvil's public dev accounts —
    attach support must not fall back to the well-known keys (review A6)."""
    known = {address.lower() for address in DEV_ACCOUNTS.values()}
    offending = [role for role in ROLES if signers[role].lower() in known]
    if offending:
        raise RunnerError(
            f"attached runs may not sign with Anvil's public dev accounts "
            f"(offending roles: {', '.join(offending)}); use dedicated burner keystores")


def bind_agreement_roles(agreement: dict, signers: dict) -> None:
    """The agreement's roles must be exactly the addresses actually signing
    (review A6) — an agreement naming anyone else never reaches the chain."""
    unbound = [role for role in ROLES
               if str(agreement.get(role, "")).lower() != signers[role].lower()]
    if unbound:
        raise RunnerError(
            f"the agreement does not bind to the actual signers for: "
            f"{', '.join(unbound)}; every role address must name its own signer")


# ---- Backend configuration (review A6) ----

class RunnerConfig:
    """Validated backend configuration. Defaults are the private local chain
    this run owns; every attached field must be named explicitly."""

    def __init__(self, rpc: str | None = None, attach: bool = False, chain_id: int | None = None,
                 keystore_dir: Path | None = None, password_file: Path | None = None,
                 agreement: Path | None = None):
        self.rpc = rpc or f"http://127.0.0.1:{LOCAL_PORT}"
        self.attach = attach
        self.chain_id = chain_id
        self.keystore_dir = keystore_dir
        self.password_file = password_file
        self.agreement = agreement or AGREEMENT


def configure(argv: list[str]) -> tuple[RunnerConfig, list[str]]:
    """Validate the backend combination up front; anything ambiguous or
    privileged by default is refused (review A6). Returns the config and the
    arguments unittest still needs."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--attach", action="store_true")
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--keystore-dir", type=Path, default=None)
    parser.add_argument("--password-file", type=Path, default=None)
    parser.add_argument("--agreement", type=Path, default=None)
    args, remaining = parser.parse_known_args(argv)
    if args.rpc and not args.attach:
        raise SystemExit("--rpc requires --attach (the runner only starts its own local chain)")
    if not args.attach:
        if args.chain_id is not None or args.keystore_dir is not None or args.password_file is not None:
            raise SystemExit(
                "--chain-id/--keystore-dir/--password-file belong to --attach; the local "
                "run signs through its own anvil and holds no key material")
        return RunnerConfig(agreement=args.agreement), remaining
    if not args.rpc:
        raise SystemExit("--attach requires --rpc <url> (name the chain you authorized)")
    if args.chain_id is None:
        raise SystemExit("--attach requires --chain-id: the expected chain must be named and compared, never guessed")
    if args.chain_id not in ALLOWED_ATTACH_CHAIN_IDS:
        raise SystemExit(
            f"chain id {args.chain_id} is not an allowed attach target "
            f"(allowed: {sorted(ALLOWED_ATTACH_CHAIN_IDS)}); a new target needs an explicit design decision")
    if args.keystore_dir is None:
        raise SystemExit(
            "--attach requires --keystore-dir holding contributor/coordinator/holder "
            "keystores; the public dev keys are never a fallback for attach")
    if args.password_file is None:
        raise SystemExit("--attach requires --password-file for those keystores")
    for role in ROLES:
        if not (args.keystore_dir / role).is_file():
            raise SystemExit(f"keystore for the {role} role not found: {args.keystore_dir / role}")
    return RunnerConfig(rpc=args.rpc, attach=True, chain_id=args.chain_id,
                        keystore_dir=args.keystore_dir, password_file=args.password_file,
                        agreement=args.agreement), remaining


CONFIG = RunnerConfig()  # replaced by configure() when the suite is invoked as a script


class ChainClient:
    """Sends role transactions without ever touching key material (review A6).

    The local backend asks this run's own anvil to sign for its unlocked dev
    accounts; the attached backend signs from encrypted keystores via cast
    with the password read from a file. Private keys never appear in argv,
    the environment or logs in either backend."""

    def __init__(self, rpc: str, senders: dict):
        self.rpc = rpc
        self.senders = senders  # role -> extra cast flags selecting that signer

    @classmethod
    def local(cls, rpc: str) -> "ChainClient":
        return cls(rpc, {role: ["--from", DEV_ACCOUNTS[role], "--unlocked"] for role in ROLES})

    @classmethod
    def attached(cls, rpc: str, keystore_dir, password_file) -> "ChainClient":
        keystore_dir, password_file = Path(keystore_dir), Path(password_file)
        return cls(rpc, {role: ["--keystore", str(keystore_dir / role),
                                "--password-file", str(password_file)] for role in ROLES})

    def cast(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["cast", *args, "--rpc-url", self.rpc],
                              capture_output=True, text=True, timeout=120)

    def _finish(self, result: subprocess.CompletedProcess) -> dict:
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError:
            receipt = {}
        receipt["_returncode"] = result.returncode
        receipt["_stderr"] = result.stderr
        return receipt

    def send(self, role: str, to: str, signature: str, *args: str) -> dict:
        result = subprocess.run(
            ["cast", "send", *self.senders[role], "--rpc-url", self.rpc,
             to, signature, *args, "--json"],
            capture_output=True, text=True, timeout=120)
        return self._finish(result)

    def send_create(self, role: str, bytecode_hex: str) -> dict:
        result = subprocess.run(
            ["cast", "send", *self.senders[role], "--rpc-url", self.rpc,
             "--create", bytecode_hex, "--json"],
            capture_output=True, text=True, timeout=120)
        return self._finish(result)


def _chain_state(client: ChainClient, contract: str) -> int:
    result = client.cast("call", contract, "state()(uint8)")
    if result.returncode != 0:
        raise AssertionError(f"state read failed: {result.stderr[:200]}")
    return int(result.stdout.strip())


_SELECTOR_CACHE: dict[str, str] = {}


def _error_selector(client: ChainClient, signature: str) -> str:
    """The 4-byte selector of a custom error, computed from the canonical
    signature with cast's keccak — cast's revert output names only errors it
    happens to know; the selector identifies ours exactly (review A5)."""
    if signature not in _SELECTOR_CACHE:
        result = subprocess.run(["cast", "keccak", signature],
                                capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise AssertionError(f"cast keccak failed for {signature}: {result.stderr[:160]}")
        _SELECTOR_CACHE[signature] = "0x" + result.stdout.strip()[2:10]
    return _SELECTOR_CACHE[signature]


def _compile_generated_example(out_dir: Path, agreement: Path) -> bytes:
    generated = out_dir / "generated"
    result = subprocess.run([sys.executable, str(LAB), "--agreement", str(agreement),
                             "--out", str(generated)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    build = out_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    # Fail-closed (review A3): a compiler or artifact failure raises and turns
    # the suite red; it must never become a green skip.
    artifacts = PINNED_SOLC.compile_with_artifacts(
        generated / "FairSettlement.sol", build, "FairSettlement")
    return bytes.fromhex(artifacts["bin"].decode().strip())


def _compile_release_fallback_variant(out_dir: Path, base_agreement: dict) -> bytes:
    """A second synthetic agreement, identical except that its dispute
    fallback releases — generated and compiled through the same pinned path,
    so the release-fallback boundary runs against real EVM too (review A5)."""
    variant = dict(base_agreement)
    variant["dispute_fallback"] = "release"
    variant["title"] = "Synthetic local-chain release-fallback exercise"
    path = out_dir / "release-fallback-agreement.json"
    path.write_text(json.dumps(variant, indent=2) + "\n", encoding="utf-8")
    return _compile_generated_example(out_dir / "variant", path)


class AnvilSettlementTests(unittest.TestCase):
    """Every exercised path runs against real EVM execution of the pinned build.

    Each scenario deploys its own fresh instance — the same isolation a
    separately authorized testnet run would use, since real chains offer no
    snapshot/revert."""

    ANVIL = None
    BYTECODE = None
    VARIANT_BYTECODE = None
    AGREEMENT_DATA = None
    CLIENT = None
    WORKDIR = None

    @classmethod
    def setUpClass(cls) -> None:
        if not _foundry_available():
            if PINNED_SOLC.tools_mandatory():
                raise AssertionError("foundry (anvil/cast) is a mandatory gate in CI but unavailable")
            raise unittest.SkipTest("foundry (anvil/cast) unavailable in this environment (optional locally)")
        cls.AGREEMENT_DATA = json.loads(CONFIG.agreement.read_text())
        cls.WORKDIR = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._cleanup)
        PINNED_SOLC.enforce_availability()
        cls.BYTECODE = _compile_generated_example(Path(cls.WORKDIR.name), CONFIG.agreement)
        cls.VARIANT_BYTECODE = _compile_release_fallback_variant(Path(cls.WORKDIR.name), cls.AGREEMENT_DATA)
        if CONFIG.attach:
            # explicit target: reachable AND the exact expected chain id (A6)
            require_chain_id(CONFIG.rpc, CONFIG.chain_id)
            signers = read_keystore_addresses(CONFIG.keystore_dir, CONFIG.password_file)
            reject_dev_account_signers(signers)
            bind_agreement_roles(cls.AGREEMENT_DATA, signers)
            cls.CLIENT = ChainClient.attached(CONFIG.rpc, CONFIG.keystore_dir, CONFIG.password_file)
        else:
            # ownership: the port is free, then OUR process serves it (A6)
            require_port_free(CONFIG.rpc)
            bind_agreement_roles(cls.AGREEMENT_DATA, DEV_ACCOUNTS)
            cls.ANVIL = start_own_anvil(LOCAL_PORT, LOCAL_CHAIN_ID)
            require_chain_id(CONFIG.rpc, LOCAL_CHAIN_ID)
            cls.CLIENT = ChainClient.local(CONFIG.rpc)

    @classmethod
    def _cleanup(cls) -> None:
        stop_own_chain(cls.ANVIL)
        cls.ANVIL = None
        if cls.WORKDIR is not None:
            cls.WORKDIR.cleanup()
            cls.WORKDIR = None

    def setUp(self) -> None:
        if CONFIG.attach and self._testMethodName in TIME_SCENARIOS:
            raise unittest.SkipTest("time-travel scenarios need the local chain clock")
        self.contract = self._deploy(self.BYTECODE)
        # The local chain clock is global and keeps the highest mined
        # timestamp, so every scenario first returns it to the present, below
        # all deadlines. Attached chains have no time control at all.
        if not CONFIG.attach:
            self._jump_to(int(time.time()))

    def _deploy(self, bytecode: bytes) -> str:
        receipt = self.CLIENT.send_create("contributor", bytecode.hex())
        address = receipt.get("contractAddress")
        if receipt.get("_returncode") != 0 or receipt.get("status") != "0x1" or not address:
            self.fail(f"deployment failed on the chain: {str(receipt)[:300]}")
        return address

    def _cast(self, *args: str) -> subprocess.CompletedProcess:
        return self.CLIENT.cast(*args)

    def _send(self, role: str, signature: str, *args: str) -> dict:
        return self.CLIENT.send(role, self.contract, signature, *args)

    def _send_ok(self, role: str, signature: str, *args: str) -> None:
        receipt = self._send(role, signature, *args)
        self.assertTrue(receipt.get("success") is True or receipt.get("status") == "0x1",
                        f"{signature} should succeed: {str(receipt)[:300]}")

    def _send_ok_in_block(self, role: str, signature: str, expected_timestamp: int, *args: str) -> dict:
        """Succeed AND land in a block mined exactly at expected_timestamp —
        the exact-boundary proof for the outer-deadline rules (review A5)."""
        receipt = self._send(role, signature, *args)
        self.assertTrue(receipt.get("status") == "0x1",
                        f"{signature} should succeed at block time {expected_timestamp}: {str(receipt)[:300]}")
        self.assertEqual(self._block_timestamp(receipt), expected_timestamp,
                         f"{signature} must execute exactly at block time {expected_timestamp}")
        return receipt

    def _send_reverts(self, role: str, signature: str, *args: str,
                      naming: str | None = None, selector: str | None = None) -> None:
        receipt = self._send(role, signature, *args)
        self.assertTrue(receipt.get("success") is False and receipt.get("status") != "0x1",
                        f"{signature} should revert: {str(receipt)[:300]}")
        if naming is not None:
            self.assertIn(naming, str(receipt),
                          f"{signature} should revert naming {naming}")
        if selector is not None:
            self.assertIn(_error_selector(self.CLIENT, selector), str(receipt),
                          f"{signature} should revert with the {selector} selector")

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

    def _next_block_at(self, timestamp: int) -> None:
        # Pin the NEXT mined block to an exact timestamp. Only ever called
        # with timestamps above the highest mined one — the boundary cases
        # (outer−1, outer, outer+1) run against real EVM, and the mined
        # timestamp is read back and asserted for every boundary send.
        result = self._cast("rpc", "anvil_setNextBlockTimestamp", str(timestamp))
        self.assertEqual(result.returncode, 0, result.stderr[:200])

    def _block_timestamp(self, receipt: dict) -> int:
        number = int(receipt["blockNumber"], 16)
        result = self._cast("block", str(number), "--json")
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        return int(json.loads(result.stdout)["data"]["timestamp"], 16)

    def _deliver(self) -> None:
        self._send_ok("holder", "recordFunding()")
        self._send_ok("contributor", "recordDelivery(uint16)", "1")

    def _deliver_and_accept(self) -> None:
        self._deliver()
        self._send_ok("coordinator", "accept(uint16)", "1")
        self.assertEqual(self._state(), STATE["accepted"])

    def test_deploys_and_starts_in_none(self) -> None:
        self.assertEqual(self._state(), STATE["none"])

    def test_happy_path_releases_on_chain(self) -> None:
        self._send_ok("holder", "recordFunding()")
        self._send_ok("contributor", "recordDelivery(uint16)", "1")
        self.assertEqual(self._state(), STATE["delivered"])
        self._send_ok("coordinator", "accept(uint16)", "1")
        self.assertEqual(self._state(), STATE["accepted"])
        self._send_ok("holder", "release()")
        self.assertEqual(self._state(), STATE["released"])

    def test_roles_hold_on_chain(self) -> None:
        with self.subTest("contributor cannot declare funding"):
            self._send_reverts("contributor", "recordFunding()", naming="NotHolder")
        self._send_ok("holder", "recordFunding()")
        with self.subTest("release before acceptance reverts"):
            self._send_reverts("holder", "release()", naming="WrongState")
        with self.subTest("coordinator cannot deliver"):
            self._send_reverts("coordinator", "recordDelivery(uint16)", "1", naming="NotContributor")
        self._send_ok("contributor", "recordDelivery(uint16)", "1")
        with self.subTest("holder cannot accept"):
            self._send_reverts("holder", "accept(uint16)", "1", naming="NotCoordinator")

    def test_review_deadline_refuses_accept_and_leaves_dispute_path(self) -> None:
        self._deliver()
        self._jump_to(self.AGREEMENT_DATA["review_deadline"] + 1)
        self._send_reverts("coordinator", "accept(uint16)", "1",
                           naming="DeadlinePassed")  # never a silent release
        self._send_ok("contributor", "openDispute(string)", "review deadline passed")
        self.assertEqual(self._state(), STATE["disputed"])

    def test_dispute_fallback_refuses_inside_window_and_refunds_after(self) -> None:
        self._deliver()
        self._send_ok("contributor", "openDispute(string)", "evidence unclear")
        # a real chain never guarantees an exact block time, so assert clearly
        # inside the window; the exact-boundary case lives in the model tests
        self._jump_to(self.AGREEMENT_DATA["dispute_deadline"] - 60)
        self._send_reverts("contributor", "applyDisputeFallback()",
                           naming="DisputeWindowStillOpen")  # window still open
        self._jump_to(self.AGREEMENT_DATA["dispute_deadline"] + 1)
        self._send_ok("contributor", "applyDisputeFallback()")  # permissionless by design
        self.assertEqual(self._state(), STATE["refunded"])

    def test_outer_deadline_refunds_from_delivered(self) -> None:
        self._deliver()
        self._jump_to(self.AGREEMENT_DATA["outer_deadline"] + 1)
        self._send_ok("coordinator", "refundAfterOuterDeadline()")
        self.assertEqual(self._state(), STATE["refunded"])

    def test_outer_deadline_release_boundaries_on_chain(self) -> None:
        """Review A5: release and refund windows are disjoint at the exact
        boundaries outer−1 / outer / outer+1, read back from mined blocks."""
        outer = self.AGREEMENT_DATA["outer_deadline"]
        for timestamp in (outer - 1, outer):
            with self.subTest(f"release still succeeds at block time {timestamp}"):
                self.contract = self._deploy(self.BYTECODE)
                self._jump_to(int(time.time()))  # the shared clock restarts per scenario
                self._deliver_and_accept()
                self._next_block_at(timestamp)
                self._send_ok_in_block("holder", "release()", timestamp)
                self.assertEqual(self._state(), STATE["released"])
        with self.subTest("the refund is still refused exactly on the deadline"):
            self.contract = self._deploy(self.BYTECODE)
            self._jump_to(int(time.time()))
            self._deliver()
            self._next_block_at(outer)
            self._send_reverts("coordinator", "refundAfterOuterDeadline()",
                               selector="OuterDeadlineNotPassed(uint256,uint256)")
        with self.subTest("one tick past it, the release is refused and the refund wins"):
            self.contract = self._deploy(self.BYTECODE)
            self._jump_to(int(time.time()))
            self._deliver_and_accept()
            self._next_block_at(outer + 1)
            self._send_reverts("holder", "release()",
                               selector="OuterDeadlinePassed(uint256,uint256)")
            self._send_ok_in_block("coordinator", "refundAfterOuterDeadline()", outer + 1)
            self.assertEqual(self._state(), STATE["refunded"])

    def test_late_release_cannot_race_the_refund_on_chain(self) -> None:
        """Review A5, the other transaction order: refund first, then the
        release attempt — the terminal state is final."""
        outer = self.AGREEMENT_DATA["outer_deadline"]
        self._deliver_and_accept()
        self._next_block_at(outer + 1)
        self._send_ok_in_block("coordinator", "refundAfterOuterDeadline()", outer + 1)
        self.assertEqual(self._state(), STATE["refunded"])
        self._send_reverts("holder", "release()", naming="WrongState")

    def test_resolved_release_refused_past_the_outer_deadline_on_chain(self) -> None:
        """Review A5: a resolution that releases can no longer execute once
        the refund window is open."""
        outer = self.AGREEMENT_DATA["outer_deadline"]
        self._deliver()
        self._send_ok("contributor", "openDispute(string)", "evidence unclear")
        self._send_ok("holder", "resolveDispute(bool)", "true")
        self._next_block_at(outer + 1)
        self._send_reverts("holder", "executeResolution()",
                           selector="OuterDeadlinePassed(uint256,uint256)")
        self._send_ok_in_block("coordinator", "refundAfterOuterDeadline()", outer + 1)
        self.assertEqual(self._state(), STATE["refunded"])

    def test_release_fallback_variant_boundaries_on_chain(self) -> None:
        """Review A5: even the AGREED release fallback loses to the refund
        priority past the outer deadline; below it, it still releases."""
        outer = self.AGREEMENT_DATA["outer_deadline"]
        dispute = self.AGREEMENT_DATA["dispute_deadline"]
        with self.subTest("the release fallback still works inside its window"):
            self.contract = self._deploy(self.VARIANT_BYTECODE)
            self._jump_to(int(time.time()))
            self._deliver()
            self._send_ok("contributor", "openDispute(string)", "holder silent")
            self._next_block_at(dispute + 1)
            self._send_ok_in_block("coordinator", "applyDisputeFallback()", dispute + 1)
            self.assertEqual(self._state(), STATE["released"])
        with self.subTest("past the outer deadline the refund outranks the agreed fallback"):
            self.contract = self._deploy(self.VARIANT_BYTECODE)
            self._jump_to(int(time.time()))
            self._deliver()
            self._send_ok("contributor", "openDispute(string)", "holder silent")
            self._next_block_at(outer + 1)
            self._send_reverts("coordinator", "applyDisputeFallback()",
                               selector="OuterDeadlinePassed(uint256,uint256)")
            self._send_ok_in_block("coordinator", "refundAfterOuterDeadline()", outer + 1)
            self.assertEqual(self._state(), STATE["refunded"])

    def test_cancellation_refunds_before_delivery(self) -> None:
        self._send_ok("holder", "recordFunding()")
        self._send_ok("holder", "refundOnCancellation()")
        self.assertEqual(self._state(), STATE["refunded"])


class AttachRehearsalTests(unittest.TestCase):
    """The attach backend, exercised end to end against a second PRIVATE
    anvil this class starts itself (review A6): fresh keystores whose key
    material exists only inside cast's encrypted files, funded on this run's
    own chain, bound to a variant agreement naming exactly those signer
    addresses. The throwaway password below guards zero-value throwaway
    keystores that can never hold value on any network; a real rehearsal
    creates its keystores interactively instead."""

    THROWAWAY = "lab-throwaway-password-for-zero-value-keys"

    CHAIN = None
    CLIENT = None
    BYTECODE = None
    AGREEMENT_DATA = None
    WORKDIR = None

    @classmethod
    def setUpClass(cls) -> None:
        if not _foundry_available():
            if PINNED_SOLC.tools_mandatory():
                raise AssertionError("foundry (anvil/cast) is a mandatory gate in CI but unavailable")
            raise unittest.SkipTest("foundry (anvil/cast) unavailable in this environment (optional locally)")
        PINNED_SOLC.enforce_availability()
        cls.WORKDIR = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._cleanup)
        workdir = Path(cls.WORKDIR.name)
        port = _free_private_port()
        rpc = f"http://127.0.0.1:{port}"
        require_port_free(rpc)
        cls.CHAIN = start_own_anvil(port, LOCAL_CHAIN_ID)
        require_chain_id(rpc, LOCAL_CHAIN_ID)
        keystore_dir = workdir / "keystores"
        keystore_dir.mkdir()
        for role in ROLES:
            result = subprocess.run(
                ["cast", "wallet", "new", str(keystore_dir), role, "--unsafe-password", cls.THROWAWAY],
                capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise AssertionError(f"could not create the {role} throwaway keystore: {result.stderr[:160]}")
        password_file = workdir / "keystore-password"
        password_file.write_text(cls.THROWAWAY, encoding="utf-8")
        signers = read_keystore_addresses(keystore_dir, password_file)
        reject_dev_account_signers(signers)  # fresh keys: the guard must agree
        variant = dict(json.loads(AGREEMENT.read_text()))
        for role in ROLES:
            variant[role] = signers[role]
        variant["title"] = "Synthetic attached rehearsal exercise"
        bind_agreement_roles(variant, signers)
        agreement_path = workdir / "attach-agreement.json"
        agreement_path.write_text(json.dumps(variant, indent=2) + "\n", encoding="utf-8")
        cls.AGREEMENT_DATA = variant
        cls.BYTECODE = _compile_generated_example(workdir, agreement_path)
        cls.CLIENT = ChainClient.attached(rpc, keystore_dir, password_file)
        # our chain, our rules: endow the burners locally, never a faucet
        for role in ROLES:
            _rpc_call(rpc, "anvil_setBalance", [signers[role], "0xde0b6b3a7640000"])

    @classmethod
    def _cleanup(cls) -> None:
        stop_own_chain(cls.CHAIN)
        cls.CHAIN = None
        if cls.WORKDIR is not None:
            cls.WORKDIR.cleanup()
            cls.WORKDIR = None

    def _deploy(self) -> str:
        receipt = self.CLIENT.send_create("contributor", self.BYTECODE.hex())
        address = receipt.get("contractAddress")
        if receipt.get("_returncode") != 0 or receipt.get("status") != "0x1" or not address:
            self.fail(f"attached deployment failed: {str(receipt)[:300]}")
        return address

    def test_attached_rehearsal_walks_the_happy_path(self) -> None:
        contract = self._deploy()
        self.assertEqual(_chain_state(self.CLIENT, contract), STATE["none"])
        self.CLIENT.send("holder", contract, "recordFunding()")
        self.CLIENT.send("contributor", contract, "recordDelivery(uint16)", "1")
        self.CLIENT.send("coordinator", contract, "accept(uint16)", "1")
        self.assertEqual(_chain_state(self.CLIENT, contract), STATE["accepted"])
        self.CLIENT.send("holder", contract, "release()")
        self.assertEqual(_chain_state(self.CLIENT, contract), STATE["released"])

    def test_attached_rehearsal_roles_hold(self) -> None:
        contract = self._deploy()
        receipt = self.CLIENT.send("contributor", contract, "recordFunding()")
        self.assertTrue(receipt.get("status") != "0x1", "wrong role must not declare funding")
        self.assertIn("NotHolder", str(receipt))
        self.assertEqual(_chain_state(self.CLIENT, contract), STATE["none"])


class CompilerGateTests(unittest.TestCase):
    """The pinned-compiler gate of this suite fails closed (review A3).

    These run wherever npx and the pinned solc exist, independent of Foundry,
    so the red-pipeline guarantee stays verifiable on stations without anvil.
    """

    @classmethod
    def setUpClass(cls) -> None:
        PINNED_SOLC.enforce_availability()

    def test_compiler_rejection_fails_the_gate_instead_of_skipping(self) -> None:
        # Mutation proof: a source the compiler rejects must turn this gate
        # red — never a green skip.
        with tempfile.TemporaryDirectory() as folder:
            workdir = Path(folder)
            broken = workdir / "Broken.sol"
            broken.write_text("contract Broken { this is not solidity }\n", encoding="utf-8")
            with self.assertRaises(PINNED_SOLC.CompileFailed):
                PINNED_SOLC.compile_with_artifacts(broken, workdir, "Broken")

    def test_generated_example_compiles_with_real_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workdir = Path(folder)
            generated = workdir / "generated"
            result = subprocess.run([sys.executable, str(LAB), "--agreement", str(AGREEMENT),
                                     "--out", str(generated)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            artifacts = PINNED_SOLC.compile_with_artifacts(
                generated / "FairSettlement.sol", workdir, "FairSettlement")
            self.assertGreater(len(artifacts["bin"]), 120, "compiled binary is implausibly small")


TIME_SCENARIOS = frozenset({
    "test_review_deadline_refuses_accept_and_leaves_dispute_path",
    "test_dispute_fallback_refuses_inside_window_and_refunds_after",
    "test_outer_deadline_refunds_from_delivered",
    "test_outer_deadline_release_boundaries_on_chain",
    "test_late_release_cannot_race_the_refund_on_chain",
    "test_resolved_release_refused_past_the_outer_deadline_on_chain",
    "test_release_fallback_variant_boundaries_on_chain",
})


_STUB_CHAIN_ID_BODY = b'{"jsonrpc":"2.0","id":1,"result":"0x7a69"}'  # 31337


class _StubChainHandler(BaseHTTPRequestHandler):
    """Answers every POST with a fixed JSON-RPC body — a foreign chain."""

    body = _STUB_CHAIN_ID_BODY

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args) -> None:
        pass


class RunnerGuardTests(unittest.TestCase):
    """The runner's own boundary guards (review A6), offline: no Foundry, no
    compiler, no network beyond a loopback stub this class starts itself."""

    def setUp(self) -> None:
        self._stub = None

    def tearDown(self) -> None:
        _StubChainHandler.body = _STUB_CHAIN_ID_BODY
        if self._stub is not None:
            self._stub.shutdown()
            self._stub.server_close()

    def _foreign_chain(self, body: bytes | None = None) -> str:
        _StubChainHandler.body = body if body is not None else _STUB_CHAIN_ID_BODY
        self._stub = ThreadingHTTPServer(("127.0.0.1", 0), _StubChainHandler)
        threading.Thread(target=self._stub.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._stub.server_address[1]}"

    def _keystore_dir(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        keystore_dir = Path(folder.name)
        for role in ROLES:
            (keystore_dir / role).write_text("{}", encoding="utf-8")
        password = keystore_dir.parent / "password"
        password.write_text("x", encoding="utf-8")
        self._password_file = password
        return keystore_dir

    def test_default_configure_targets_the_owned_local_chain(self) -> None:
        config, remaining = configure([])
        self.assertFalse(config.attach)
        self.assertEqual(config.rpc, f"http://127.0.0.1:{LOCAL_PORT}")
        self.assertIsNone(config.chain_id)
        self.assertEqual(remaining, [])

    def test_configure_refuses_rpc_without_attach(self) -> None:
        with self.assertRaises(SystemExit):
            configure(["--rpc", "http://127.0.0.1:9999"])

    def test_configure_refuses_attach_without_every_explicit_field(self) -> None:
        keystore_dir = self._keystore_dir()
        cases = (
            ["--attach"],
            ["--attach", "--rpc", "http://127.0.0.1:9999"],
            ["--attach", "--rpc", "http://127.0.0.1:9999", "--chain-id", "11155111"],
            ["--attach", "--rpc", "http://127.0.0.1:9999", "--chain-id", "11155111",
             "--keystore-dir", str(keystore_dir)],  # no password file
        )
        for argv in cases:
            with self.subTest(" ".join(argv)):
                with self.assertRaises(SystemExit):
                    configure(argv)

    def test_configure_refuses_disallowed_and_unnamed_chain_targets(self) -> None:
        keystore_dir = self._keystore_dir()
        for chain_id in ("1", "999999"):  # mainnet and unnamed targets stay out
            with self.subTest(f"chain id {chain_id}"):
                with self.assertRaises(SystemExit):
                    configure(["--attach", "--rpc", "http://127.0.0.1:9999",
                               "--chain-id", chain_id, "--keystore-dir", str(keystore_dir),
                               "--password-file", str(self._password_file)])

    def test_configure_refuses_incomplete_keystore_sets(self) -> None:
        keystore_dir = self._keystore_dir()
        (keystore_dir / "holder").unlink()
        with self.assertRaises(SystemExit):
            configure(["--attach", "--rpc", "http://127.0.0.1:9999", "--chain-id", "11155111",
                       "--keystore-dir", str(keystore_dir), "--password-file", str(self._password_file)])

    def test_configure_refuses_attach_flags_on_the_local_run(self) -> None:
        for argv in (["--chain-id", "31337"], ["--keystore-dir", "/tmp"], ["--password-file", "/tmp"]):
            with self.subTest(argv[0]):
                with self.assertRaises(SystemExit):
                    configure(argv)

    def test_configure_accepts_a_fully_named_allowed_attach_target(self) -> None:
        keystore_dir = self._keystore_dir()
        config, _ = configure(["--attach", "--rpc", "http://127.0.0.1:9999", "--chain-id", "11155111",
                               "--keystore-dir", str(keystore_dir), "--password-file", str(self._password_file)])
        self.assertTrue(config.attach)
        self.assertEqual(config.chain_id, 11155111)

    def test_a_foreign_responding_instance_is_not_our_test_server(self) -> None:
        rpc = self._foreign_chain()
        with self.assertRaisesRegex(RunnerError, "not this suite's test server"):
            require_port_free(rpc)

    def test_a_silent_port_is_free_for_our_own_process(self) -> None:
        rpc = f"http://127.0.0.1:{_free_private_port()}"
        self.assertIsNone(require_port_free(rpc))

    def test_the_chain_id_is_compared_not_assumed(self) -> None:
        rpc = self._foreign_chain()
        require_chain_id(rpc, 31337)  # the stub really answers 31337
        with self.assertRaisesRegex(RunnerError, "identifies as 31337, expected 11155111"):
            require_chain_id(rpc, 11155111)

    def test_a_broken_rpc_answer_fails_the_id_check(self) -> None:
        rpc = self._foreign_chain(b"not json at all")
        with self.assertRaises(RunnerError):
            require_chain_id(rpc, 31337)

    def test_attached_signers_may_not_be_the_public_dev_accounts(self) -> None:
        with self.assertRaisesRegex(RunnerError, "public dev accounts"):
            reject_dev_account_signers(dict(DEV_ACCOUNTS))
        mixed = dict(DEV_ACCOUNTS)
        mixed["contributor"] = "0x9858EfFD232B4033E47d90003D44EC97040789d2"
        mixed["coordinator"] = "0x2c143e2883b993d26b3e134f2fafee5b199f4e95"
        with self.assertRaisesRegex(RunnerError, "holder"):  # only holder still dev
            reject_dev_account_signers(mixed)
        reject_dev_account_signers(mixed | {  # all fresh addresses pass
            "holder": "0x8d87a1b16af4d1c6f8e3f7dc2a0e47d21a3f4a5c"})

    def test_agreement_roles_must_bind_to_the_actual_signers(self) -> None:
        signers = dict(DEV_ACCOUNTS)
        bind_agreement_roles(dict(DEV_ACCOUNTS), signers)  # exact match binds
        upper = {role: address.upper() for role, address in signers.items()}
        bind_agreement_roles(dict(DEV_ACCOUNTS), upper)  # case is not a role
        stranger = dict(signers)
        stranger["holder"] = "0x0000000000000000000000000000000000000004"
        with self.assertRaisesRegex(RunnerError, "holder"):
            bind_agreement_roles(dict(DEV_ACCOUNTS), stranger)


if __name__ == "__main__":
    CONFIG, remaining = configure(sys.argv[1:])
    unittest.main(argv=["test-settlement-anvil", *remaining])
