from google.adk.tools import FunctionTool
from google.cloud import discoveryengine_v1 as discoveryengine
import os
from google.api_core.exceptions import PermissionDenied
from typing import Dict, Any, List

def search_evaluator_datastore(query: str) -> Dict[str, Any]:
    """
    Searches the project's specialized knowledge base of documents related to
    polymer compounding, material properties, and processing. Use this to answer
    technical questions about the project's domain.

    Args:
        query: The user's search query string.

    Returns:
        A dictionary with a 'hits' key containing a list of search results,
        or an 'error' key if the search failed.
    """
    try:
        gcp_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        gcp_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        datastore_id = os.environ.get("EVALUATOR_DATASTORE_ID")

        missing_vars = []
        if not gcp_project_id: missing_vars.append("GOOGLE_CLOUD_PROJECT")
        if not gcp_location: missing_vars.append("GOOGLE_CLOUD_LOCATION")
        if not datastore_id: missing_vars.append("EVALUATOR_DATASTORE_ID")
        if missing_vars:
            return {
                "hits": [],
                "summary": f"Search failed because the tool is misconfigured. The following environment variables are missing: {', '.join(missing_vars)}.",
                "error": "TOOL_UNAVAILABLE"
            }

        client = discoveryengine.SearchServiceClient()

        serving_config = client.serving_config_path(
            project=gcp_project_id,
            location=gcp_location,
            data_store=datastore_id,
            serving_config="default_search",
        )

        request = discoveryengine.SearchRequest(
            serving_config=serving_config, query=query, page_size=5
        )
        response = client.search(request)

        hits: List[Dict[str, Any]] = [
            {"id": result.document.id, "content": result.document.content}
            for result in response.results
            if result.document.content
        ]

        if not hits:
            return {
                "hits": [],
                "summary": "No relevant information found in the project knowledge base for this query.",
            }

        return {
            "hits": hits,
            "summary": f"Found {len(hits)} relevant snippets from the knowledge base.",
        }
    except PermissionDenied as e:
        return {
            "hits": [],
            "summary": "Search failed due to a permissions error. The service account or user running the agent needs the 'Discovery Engine User' IAM role.",
            "error": "PermissionDenied",
        }
    except Exception as e:
        return {
            "hits": [],
            "summary": f"An unexpected error occurred during search: {str(e)}",
            "error": f"{type(e).__name__}",
        }

evaluator_search_tool = FunctionTool(search_evaluator_datastore)