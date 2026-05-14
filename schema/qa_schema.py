from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"

class Bug(BaseModel):
    file_path: str = Field(..., description="Path to the file containing the bug")
    line_number: Optional[str] = Field(default=None, description="Line number or range where the bug exists")
    severity: Severity = Field(..., description="Severity of the bug")
    description: str = Field(..., description="Detailed description of the issue")
    suggested_fix: str = Field(..., description="Actionable suggestion to fix the bug")

class TestCase(BaseModel):
    test_name: str = Field(..., description="Name of the test case")
    test_file_path: str = Field(..., description="Path where this test file should be saved, e.g., 'tests/test_auth.py'")
    description: str = Field(..., description="What this test verifies")
    test_code: str = Field(..., description="The complete source code for the test")
    target_file: str = Field(..., description="The source code file this test is targeting")

class QAServiceReport(BaseModel):
    service_name: str = Field(..., description="Name of the service being reviewed")
    bugs: List[Bug] = Field(default_factory=list, description="Bugs found in this service")
    test_cases: List[TestCase] = Field(default_factory=list, description="Test cases generated for this service")
    code_quality_score: int = Field(..., description="Score from 1 to 10 for this service's code quality")

class QASchema(BaseModel):
    overall_assessment: str = Field(..., description="Executive summary of the project's overall code quality")
    service_reports: List[QAServiceReport] = Field(default_factory=list, description="Detailed QA reports for each service")
    critical_issues: int = Field(default=0, description="Total number of critical severity bugs found")
    total_bugs_found: int = Field(default=0, description="Total number of bugs found across all severities")
    total_tests_written: int = Field(default=0, description="Total number of test cases generated")
    recommendations: List[str] = Field(default_factory=list, description="Actionable recommendations to improve code quality")
    passed: bool = Field(default=False, description="True if no critical issues and all service scores are >= 7")
