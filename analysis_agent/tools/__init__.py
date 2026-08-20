from analysis_agent.tools.ingest_results import ingest_results
from analysis_agent.tools.analyze_metrics import analyze_metrics
from analysis_agent.tools.fetch_xray_traces import fetch_xray_traces
from analysis_agent.tools.fetch_cloudwatch_metrics import fetch_cloudwatch_metrics
from analysis_agent.tools.identify_root_cause import identify_root_cause
from analysis_agent.tools.generate_recommendations import generate_recommendations
from analysis_agent.tools.evaluate_slos import evaluate_slos
from analysis_agent.tools.compare_runs import compare_runs

ALL_TOOLS = [
    ingest_results,
    analyze_metrics,
    fetch_xray_traces,
    fetch_cloudwatch_metrics,
    identify_root_cause,
    generate_recommendations,
    evaluate_slos,
    compare_runs,
]
