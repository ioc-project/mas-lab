#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
Integration test demonstrating the contract testing framework.

This test implements the example from the MaskAT formal verification paper,
testing a "No PII Leakage" governance hook.
"""

import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from mas.lab.verification.contracts import ComplianceReporter, ContractExecutor, ContractLoader, TestGenerator

# Contract fixtures live in mas-lab/components/core/contracts/
_CONTRACT_FILE = (
    Path(__file__).parent.parent.parent.parent / "components" / "core" / "contracts" / "no-pii-leakage.yaml"
)


class PolicyViolation(Exception):
    """Raised when an action violates governance policy."""

    pass


class PIIGovernanceHook:
    """
    Example governance hook that blocks PII in messages.

    This is a naive regex-based implementation for demonstration.
    The contract tests will expose its weaknesses.
    """

    # Simple regex patterns for PII detection
    SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    CREDIT_CARD_PATTERN = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b")
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

    def evaluate_action(self, action: dict) -> str:
        """
        Evaluate an action against the "No PII" policy.

        Returns:
            "allow" or "block"

        Raises:
            PolicyViolation: If PII is detected
        """
        # Extract message body
        if action.get("type") != "tool_call":
            return "allow"

        args = action.get("args", {})
        message = args.get("body", "")

        # Check for PII patterns
        if self.SSN_PATTERN.search(message):
            raise PolicyViolation("SSN detected in message")

        if self.CREDIT_CARD_PATTERN.search(message):
            raise PolicyViolation("Credit card number detected in message")

        if self.EMAIL_PATTERN.search(message):
            raise PolicyViolation("Email address detected in message")

        return "allow"


class TestContractFramework:
    """Test the contract testing framework end-to-end."""

    def test_load_contract(self):
        """Test loading a YAML contract file."""
        loader = ContractLoader()
        contract = loader.load(_CONTRACT_FILE)

        assert contract.name == "No PII Leakage"
        assert contract.type == "governance"
        assert len(contract.test_cases) > 0
        assert contract.fuzzing.enabled is True

    def test_execute_governance_contract(self):
        """Test executing Contract 3 (Governance Enforcement) tests."""
        # Load the contract
        loader = ContractLoader()
        contract = loader.load(_CONTRACT_FILE)

        # Create the implementation to test
        hook = PIIGovernanceHook()

        # Execute tests
        executor = ContractExecutor()
        report = executor.execute_governance_contract(contract=contract, implementation=hook, verbose=True)

        # Print results
        reporter = ComplianceReporter()
        reporter.print_console_summary([report], verbose=True)

        # Assertions
        assert report.total_tests == len(contract.test_cases)
        assert report.passed > 0, "Some tests should pass"

        # Expected to fail due to naive implementation:
        # - "SS N: 123-45-6789" (adversarial spacing) will bypass regex
        # - "ssn: 123456789" (no dashes) will bypass regex
        print(f"\n✅ Test passed: {report.passed} / {report.total_tests}")
        print(f"❌ Test failed: {report.failed}")
        print(f"⚠️  Ambiguous: {report.ambiguous}")
        print(f"🔧 Errors: {report.errors}")
        print(f"📊 Success rate: {report.success_rate:.1f}%")

    def test_generate_pii_tests(self):
        """Test automatic test generation for PII policy."""
        generator = TestGenerator()
        tests = generator.generate_pii_tests()

        assert len(tests) > 0

        # Verify boundary tests
        boundary_tests = [t for t in tests if "adversarial" not in t.rationale.lower()]
        assert any(t.expected == "allow" for t in boundary_tests)
        assert any(t.expected == "block" for t in boundary_tests)

        # Verify fuzzing tests
        fuzz_tests = [t for t in tests if "adversarial" in t.rationale.lower()]
        assert len(fuzz_tests) > 0

        print(f"\n✅ Generated {len(tests)} tests:")
        print(f"   - {len(boundary_tests)} boundary tests")
        print(f"   - {len(fuzz_tests)} fuzzing tests")

    def test_improved_pii_hook(self, tmp_path: Path):
        """
        Test an improved PII hook that handles edge cases.

        This demonstrates iterative improvement based on contract test failures.
        """

        class ImprovedPIIHook(PIIGovernanceHook):
            """Improved hook that handles adversarial cases."""

            # Enhanced patterns
            SSN_PATTERN = re.compile(r"\b\d{3}[\s-]?\d{2}[\s-]?\d{4}\b", re.IGNORECASE)
            EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+[\s\[\(]?@[\s\]\)]?[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

            def evaluate_action(self, action: dict) -> str:
                # Extract message
                if action.get("type") != "tool_call":
                    return "allow"

                args = action.get("args", {})
                message = args.get("body", "")

                # Normalize message (remove extra spaces, "SS N" -> "SSN")
                normalized = re.sub(r"\s+", "", message)

                # Check patterns on normalized text
                if self.SSN_PATTERN.search(normalized):
                    raise PolicyViolation("SSN detected")

                if self.CREDIT_CARD_PATTERN.search(normalized):
                    raise PolicyViolation("Credit card detected")

                if self.EMAIL_PATTERN.search(message):
                    raise PolicyViolation("Email detected")

                return "allow"

        # Load contract
        loader = ContractLoader()
        contract = loader.load(_CONTRACT_FILE)

        # Test improved hook
        executor = ContractExecutor()
        report = executor.execute_governance_contract(
            contract=contract, implementation=ImprovedPIIHook(), verbose=False
        )

        reporter = ComplianceReporter()
        reporter.print_console_summary([report])

        # Improved hook should pass more tests
        assert report.passed > 0
        print(f"\n✅ Improved hook passed: {report.passed} / {report.total_tests}")
        print(f"📊 Success rate: {report.success_rate:.1f}%")

        # Generate compliance report under pytest tmp dir (never repo root)
        json_report = tmp_path / "compliance-report.json"
        md_report = tmp_path / "COMPLIANCE_REPORT.md"
        reporter.generate_json_report([report], json_report)
        reporter.generate_markdown_report([report], md_report)
        print(f"\n📄 Reports generated: {json_report}, {md_report}")


if __name__ == "__main__":
    # Run tests manually
    test = TestContractFramework()

    print("=" * 60)
    print("CONTRACT TESTING FRAMEWORK - INTEGRATION TEST")
    print("=" * 60)

    print("\n[1/4] Loading contract from YAML...")
    test.test_load_contract()
    print("✅ Contract loaded successfully")

    print("\n[2/4] Executing governance contract tests...")
    test.test_execute_governance_contract()

    print("\n[3/4] Testing automatic test generation...")
    test.test_generate_pii_tests()

    print("\n[4/4] Testing improved PII hook...")
    test.test_improved_pii_hook()

    print("\n" + "=" * 60)
    print("✅ ALL INTEGRATION TESTS COMPLETE")
    print("=" * 60)
