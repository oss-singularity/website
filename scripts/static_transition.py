"""Offline transitions exclusively in self-created fixtures, never a deployer.

Preparation retains the current complete product check. Shared journaling lives
in static_engine; this public fixture API never accepts an existing destination.
"""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path

import static_engine as engine
import static_fixture as fs
from site_artifact import capture, read_external, require, write_descriptor

Attempt = engine.Attempt
TERMINAL = engine.TERMINAL
PHASES = engine.PHASES


def product_capture(root):
    specification = importlib.util.spec_from_file_location(
        'transition_product_artifact', Path(__file__).with_name('release-artifact.py'))
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.inspect(root)


@contextmanager
def session(handle, checkpoint=None):
    """One cooperative lock covers preparation, mutation and recovery.

    checkpoint is a trusted test harness callback at fixed boundary names, never
    supplied artifact behavior. A hard process exit is deliberately not caught.
    """
    with fs.locked(handle) as descriptors:
        yield Transition(handle, descriptors, checkpoint)


class Transition(engine.Transition):
    def __init__(self, handle, descriptors, checkpoint):
        super().__init__(handle, descriptors, checkpoint, fs.check_anchors)

    def prepare(self, *, candidate_root, candidate_descriptor, expected_candidate_commit,
                predecessor_root, predecessor_descriptor):
        """Read only artifact paths; the writable target always comes from create_fixture."""
        self._load()
        require(self.state['attempt'] is None or self.attempt['phase'] in TERMINAL, 'unfinished_attempt')
        roots = [Path(os.path.abspath(path)) for path in (candidate_root, predecessor_root)]
        fixture_root = Path(self.handle.container)
        require(not any(path.is_relative_to(fixture_root) or fixture_root.is_relative_to(path) for path in roots)
                and not roots[0].is_relative_to(roots[1]) and not roots[1].is_relative_to(roots[0]),
                'overlapping_roots')
        candidate = product_capture(candidate_root)
        predecessor = capture(predecessor_root, lambda names, _read: names)
        return self._prepare_captured(
            candidate=candidate, candidate_descriptor=read_external(candidate_descriptor, 4096),
            expected_candidate_commit=expected_candidate_commit,
            predecessor=predecessor, predecessor_descriptor=read_external(predecessor_descriptor, 4096))

    def report(self):
        return {**super().report(), 'kind': 'static-transition-fixture', 'fixture_only': True}

    def write_report(self, destination):
        write_descriptor(destination, engine.plan.canonical(self.report()) + b'\n',
                         Path(self.handle.container) / 'target')
