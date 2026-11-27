"""One owner for a block's published, executed and displayed code."""

import ast
from dataclasses import dataclass, field

from app.models import (
    Block,
    ExecResult,
    Extraction,
    LabelDecision,
    OutputCheck,
    OutputMatch,
    PatchVerdict,
    RepairAttempt,
    StepKind,
    StepReport,
)
from app.output_check import check_output, repl_shows_multiple_values


@dataclass
class Step:
    block: Block
    result: ExecResult | None = None
    decision: LabelDecision | None = None
    attempts: list[RepairAttempt] = field(default_factory=list)
    accepted: RepairAttempt | None = None
    output_check: OutputCheck | None = None

    @property
    def idx(self) -> int:
        return self.block.idx

    @property
    def runnable(self) -> bool:
        return self.block.runnable_code is not None

    @property
    def is_python(self) -> bool:
        return self.block.kind in (StepKind.PYTHON, StepKind.REPL)

    def record_run(self, result: ExecResult | None, decision: LabelDecision) -> None:
        self.result, self.decision = result, decision

    def record_attempt(self, attempt: RepairAttempt) -> None:
        self.attempts.append(attempt)

    def accept(self, attempt: RepairAttempt) -> None:
        if attempt.verdict not in (PatchVerdict.FIXED, PatchVerdict.FIXED_UNVERIFIED):
            raise ValueError("Only a sandbox-verified patch can be accepted")
        self.accepted = attempt

    @property
    def executed_code(self) -> str | None:
        return self.accepted.patched_code if self.accepted else self.block.runnable_code

    @property
    def final_result(self) -> ExecResult | None:
        return self.accepted.result if self.accepted else self.result

    def compare_output(self, earlier_code: str) -> None:
        shown, final = self.block.shown_output, self.final_result
        if shown is None or final is None:
            return
        if self.block.kind == StepKind.REPL and repl_shows_multiple_values(
            self.block.runnable_code or ""
        ):
            self.output_check = OutputCheck(
                status=OutputMatch.NOT_COMPARED,
                reason="This REPL block shows several values, but only the last one is captured.",
                shown=shown,
                actual=final.stdout,
            )
            return
        self.output_check = check_output(shown, final, self.executed_code or "", earlier_code)

    @property
    def display_code(self) -> str:
        if self.accepted is None:
            return self.block.code
        magics = (
            []
            if self.block.kind == StepKind.REPL
            else [
                line
                for line in self.block.code.splitlines()
                if line.lstrip().startswith(("!", "%"))
            ]
        )
        return "\n".join([*magics, self.accepted.patched_code])

    def bound_names(self) -> set[str]:
        if not self.is_python:
            return set()
        try:
            tree = ast.parse(self.executed_code or "")
        except (SyntaxError, ValueError):
            return set()
        names: set[str] = set()

        class Bindings(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    names.add(node.id)

            def visit_Import(self, node: ast.Import) -> None:
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                names.update(
                    alias.asname or alias.name for alias in node.names if alias.name != "*"
                )

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                names.add(node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                names.add(node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                names.add(node.name)

            def visit_Lambda(self, node: ast.Lambda) -> None:
                pass

            def visit_ListComp(self, node: ast.ListComp) -> None:
                pass

            def visit_SetComp(self, node: ast.SetComp) -> None:
                pass

            def visit_DictComp(self, node: ast.DictComp) -> None:
                pass

            def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
                pass

        Bindings().visit(tree)
        return names

    def to_report(self) -> StepReport:
        final = self.final_result
        output = final.stdout if final else ""
        if final and final.stderr:
            output += ("\n\n" if output else "") + final.stderr
        verdict = (
            self.accepted.verdict
            if self.accepted
            else PatchVerdict.FAILED
            if self.attempts
            else None
        )
        return StepReport(
            idx=self.idx,
            kind=self.block.kind,
            original_code=self.block.code,
            final_code=self.display_code,
            label=self.decision.label if self.decision else None,
            reason=self.decision.reason if self.decision else "",
            verdict=verdict,
            repair_attempts=list(self.attempts),
            explanation=self.accepted.explanation if self.accepted else "",
            apis_changed=self.accepted.apis_changed if self.accepted else [],
            output=output,
            output_check=self.output_check,
            error=final.error if final else None,
            warnings=final.warnings if final else [],
        )


def build_steps(extraction: Extraction) -> list[Step]:
    return [Step(block) for block in extraction.blocks]
