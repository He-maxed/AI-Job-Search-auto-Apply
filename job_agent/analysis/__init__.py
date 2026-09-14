"""Job description analysis: strict structured extraction via any LLMProvider."""

from job_agent.analysis.model import JobAnalysis, SalaryRange
from job_agent.analysis.parse import MalformedAnalysisError, parse_analysis
from job_agent.analysis.service import analyze_job, enrich_job_with_analysis

__all__ = [
    "JobAnalysis",
    "SalaryRange",
    "MalformedAnalysisError",
    "analyze_job",
    "enrich_job_with_analysis",
    "parse_analysis",
]