import copy
from dataclasses import dataclass, field
import functools
import os
from typing import List, Dict, Optional, Callable, Optional, Tuple, Iterable

from pycparser import CParser, c_ast as ca

from .compiler import Compiler
from .randomizer import Randomizer
from .scorer import Scorer
from .perm.perm import EvalState, Perm
from .helpers import try_remove
from .profiler import Profiler
from . import perm
from . import ast_util


@dataclass
class CandidateResult:
    """
    Represents the result of scoring a candidate, and is sent from child to
    parent processes.
    """

    score: int
    hash: str
    source: Optional[str]
    profiler: Profiler = field(default_factory=Profiler)


@dataclass
class Candidate:
    """
    Represents a AST candidate created from a source which can be randomized
    (possibly multiple times), compiled, and scored.
    """

    ast: ca.FileAST

    orig_fn: ca.FuncDef
    fn_index: int
    rng_seed: int
    randomizer: Randomizer
    score_value: Optional[int] = field(init=False, default=None)
    score_hash: Optional[str] = field(init=False, default=None)
    _cache_source: Optional[str] = field(init=False, default=None)

    @staticmethod
    @functools.lru_cache(maxsize=16)
    def _cached_shared_ast(
        source: str, fn_name: str
    ) -> Tuple[ca.FuncDef, int, ca.FileAST]:
        parser = CParser()
        ast = parser.parse(source)
        orig_fn, fn_index = ast_util.extract_fn(ast, fn_name)
        ast_util.normalize_ast(orig_fn, ast)
        return orig_fn, fn_index, ast

    @staticmethod
    def from_source(source: str, fn_name: str, rng_seed: int) -> "Candidate":
        # Use the same AST for all instances of the same original source, but
        # with the target function deeply copied. Since we never change the
        # AST outside of the target function, this is fine, and it saves us
        # performance (deepcopy is really slow).
        orig_fn, fn_index, ast = Candidate._cached_shared_ast(source, fn_name)
        ast = copy.copy(ast)
        ast.ext = copy.copy(ast.ext)
        ast.ext[fn_index] = copy.deepcopy(orig_fn)
        return Candidate(
            ast=ast,
            orig_fn=orig_fn,
            fn_index=fn_index,
            rng_seed=rng_seed,
            randomizer=Randomizer(rng_seed),
        )

    def randomize_ast(self) -> None:
        self.randomizer.randomize(self.ast, self.fn_index)
        self._cache_source = None

    def get_source(self) -> str:
        if self._cache_source is None:
            self._cache_source = ast_util.to_c(self.ast)
        return self._cache_source

    def compile(self, compiler: Compiler, show_errors: bool = False) -> Optional[str]:
        source: str = self.get_source()
        return compiler.compile(source, show_errors=show_errors)

    def score(self, scorer: Scorer, o_file: Optional[str]) -> CandidateResult:
        self.score_value = None
        self.score_hash = None
        try:
            self.score_value, self.score_hash = scorer.score(o_file)
        finally:
            if o_file:
                try_remove(o_file)
        return CandidateResult(
            score=self.score_value, hash=self.score_hash, source=self.get_source()
        )
