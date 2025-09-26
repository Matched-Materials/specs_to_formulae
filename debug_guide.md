Gotta fix the RAG database. 
Need to make a service account that has the appropriate IAMs. 
What to configure
1) Identity to call Discovery Engine

Use a service account for anything your code runs (local or CI). User auth via gcloud auth login is not what google-cloud libraries use by default.

Two working options:

Local dev (quick):

gcloud auth application-default login


This creates ADC for your user. Works, but is ephemeral and tied to your shell/user.

Recommended (stable / CI):

Create a service account:

gcloud iam service-accounts create rag-tool \
  --display-name="RAG Tool Account"


Grant roles (see roles below).

Create a key only if needed for local dev:

gcloud iam service-accounts keys create sa.json \
  --iam-account=rag-tool@${PROJECT}.iam.gserviceaccount.com
export GOOGLE_APPLICATION_CREDENTIALS=$PWD/sa.json


(In production, attach the SA to the runtime; avoid keys if you can.)

2) IAM roles you actually need

You need the permission discoveryengine.servingConfigs.search on the serving config of your data store (or project level).

Easiest path to green (then tighten later):

On the project (fastest to verify):

roles/discoveryengine.admin (broad; for initial bring-up)

Or minimally, the role that includes servingConfigs.search (e.g., a Search/Discovery “Viewer/User” role that can call search). If you’re unsure which, start with admin, confirm it works, then ratchet down with:

gcloud iam roles describe roles/discoveryengine.viewer --project $PROJECT
# check if 'discoveryengine.servingConfigs.search' is included


If you’re calling Vertex models too (Gemini on Vertex), add:

roles/aiplatform.user (Vertex AI User)

Granting example:

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:rag-tool@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/discoveryengine.admin"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:rag-tool@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

3) APIs + resource correctness

Enable APIs:

gcloud services enable discoveryengine.googleapis.com aiplatform.googleapis.com


Make sure your resource path matches your data store:

projects/$PROJECT/locations/$LOCATION/dataStores/$DATA_STORE/servingConfigs/default_search


(Your logs showed us-central1 and a dataStores/.../servingConfigs/default_search, so you’re on the dataStore flavor, not the newer “engines” path.)

Sanity-check the serving config exists:

gcloud discovery-engine serving-configs list \
  --project=$PROJECT --location=$LOCATION --data-store=$DATA_STORE

4) Point your tool at the right things (env + code)

Set env so the tool can build the path deterministically:

export GCP_PROJECT=agent-testing-463323
export DISCOVERY_LOCATION=us-central1
export DISCOVERY_DATA_STORE=evaluator-connector_1756340136792
# If using a SA key locally:
export GOOGLE_APPLICATION_CREDENTIALS=$PWD/sa.json


Tool skeleton (robust + ADK-friendly):

# evaluator/matsi_property_evaluator/tools.py
import os, json
from google.cloud import discoveryengine_v1 as de

def evaluator_search_tool(query: str, page_size: int = 5) -> str:
    """Look up evaluator snippets. Returns JSON or 'TOOL_UNAVAILABLE: ...' on failure."""
    try:
        client = de.SearchServiceClient()
        serving_config = client.serving_config_path(
            project=os.environ["GCP_PROJECT"],
            location=os.environ.get("DISCOVERY_LOCATION", "us-central1"),
            data_store=os.environ["DISCOVERY_DATA_STORE"],
            serving_config="default_search",
        )
        req = de.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=page_size,
            content_search_spec=de.SearchRequest.ContentSearchSpec(
                snippet_spec=de.SearchRequest.ContentSearchSpec.SnippetSpec(
                    max_snippet_count=1
                )
            ),
        )
        resp = client.search(request=req)

        items = []
        for r in resp:
            doc = r.document
            items.append({
                "name": doc.name,  # full resource name
                "title": doc.derived_struct_data.get("title"),
                "uri": doc.derived_struct_data.get("link"),
                "snippet": getattr(r, "snippet_info", None) and r.snippet_info.snippet
            })

        return json.dumps({"results": items})

    except Exception as e:
        # IMPORTANT: never throw—return a sentinel the agent was instructed to ignore
        return f"TOOL_UNAVAILABLE: {type(e).__name__}: {e}"


Your prompt already says: “If the tool response starts with TOOL_UNAVAILABLE, ignore it…”—perfect. That prevents malformed tool text from contaminating the final JSON that must satisfy output_schema.

5) Common pitfalls that cause your exact error

Only gcloud auth login was done (interactive user), but no ADC:

Fix: gcloud auth application-default login or use a service account key + GOOGLE_APPLICATION_CREDENTIALS.

Wrong project / location / data store id in the tool path:

If the resource name is wrong, you’ll get 404 or permission errors. List the serving configs to verify.

Process/threads don’t see env vars:

Make sure the env vars are exported in the same shell/IDE run configuration that launches your Python process.

Insufficient role:

If you only granted a “viewer” that doesn’t include servingConfigs.search, you’ll get PERMISSION_DENIED. Start high (admin) to confirm, then least-privilege down.

Letting exceptions bubble to the model:

If your tool raises, ADK dumps traces into the conversation, and the model may embed it in its final “JSON”. Always catch and return the sentinel string.

6) Quick end-to-end verification

Verify ADC:

python -c "import google.auth; print(google.auth.default())"


You should see your service account (or user) + project.

Verify Discovery Engine call in the same venv:

from google.cloud import discoveryengine_v1 as de, exceptions
import os
c = de.SearchServiceClient()
path = c.serving_config_path(os.environ["GCP_PROJECT"], os.environ["DISCOVERY_LOCATION"], os.environ["DISCOVERY_DATA_STORE"], "default_search")
print(path)
try:
    list(c.search(request=de.SearchRequest(serving_config=path, query="test", page_size=1)))
    print("OK")
except exceptions.PermissionDenied as e:
    print("PERMISSION", e)


Run your ADK flow again with tools=[evaluator_search_tool]. If it fails, your tool should now return TOOL_UNAVAILABLE: ... but the final agent JSON will still validate—no more Pydantic blow-ups.