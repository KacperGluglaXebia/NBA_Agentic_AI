"""HoopsPass assistant as code: one LangGraph agent whose tools all come from Databricks managed MCP servers.

This is the code-first twin of the no-code Supervisor Agent of demo C. Same three kinds of tools:

* Genie agent B (``/api/2.0/mcp/genie/<space_id>``) for any question about the numbers,
* the policy index (``/api/2.0/mcp/vector-search/<catalog>/knowledge/policy_chunks_index``) for rules and definitions,
* the read-only functions of ``<catalog>.agent_tools`` (``/api/2.0/mcp/functions/<catalog>/agent_tools``).

The agent never sees a table, a credential or a governance function: it gets what the three MCP servers expose, and
every call runs with the identity of the caller, so the row filter and the masks of ``02_curate_and_govern`` still apply.

MLflow logs this FILE (models from code). Configuration comes from ``agent_config.yaml`` next to it, or from the YAML
named in ``HOOPSPASS_AGENT_CONFIG`` (notebook 04 writes one with the resolved Genie space id), or from ``model_config``
at ``log_model`` time.
"""
import asyncio
import os
import threading
from typing import Generator
from uuid import uuid4

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_langchain import ChatDatabricks, DatabricksMCPServer, DatabricksMultiServerMCPClient
from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from mlflow.models import ModelConfig
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse, ResponsesAgentStreamEvent

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.yaml")
config = ModelConfig(development_config=os.environ.get("HOOPSPASS_AGENT_CONFIG", _DEFAULT_CONFIG))

# One agent turn is a model node plus a tools node; Genie needs a query call and usually one or two polls.
RECURSION_LIMIT = 16


_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()


def run_async(coroutine):
    """Run a coroutine on one long-lived event loop in a background thread, from any calling thread.

    MCP sessions and the LangGraph agent are bound to the loop they were created on. Notebooks already run a loop,
    and mlflow.genai.evaluate or Model Serving call predict from worker threads, so a fresh asyncio.run per call
    fails with "bound to a different event loop". One dedicated loop avoids both problems.
    """
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="hoopspass-agent-loop", daemon=True).start()
    return asyncio.run_coroutine_threadsafe(coroutine, _LOOP).result()


def mcp_servers(host: str) -> dict:
    """Label -> full URL of every managed MCP server the agent may use."""
    catalog = config.get("catalog")
    return {
        "genie": f"{host}/api/2.0/mcp/genie/{config.get('genie_space_id')}",
        "policies": f"{host}/api/2.0/mcp/vector-search/{catalog}/knowledge/policy_chunks_index",
        "tools": f"{host}/api/2.0/mcp/functions/{catalog}/agent_tools",
    }


def route_of(tool_name: str) -> str:
    """Short, stable route label for an MCP tool name (names carry catalog, schema or space id)."""
    name = tool_name.lower()
    if "policy_chunks_index" in name:
        return "search_policies"
    if "order_lookup" in name:
        return "order_lookup"
    if "team_season_summary" in name:
        return "team_season_summary"
    if config.get("genie_space_id").lower() in name or "genie" in name:
        return "genie"
    return name


def tools_used(state: dict) -> list:
    """Route labels of the tools the agent actually called in this answer (one per call)."""
    return [route_of(m.name) for m in state["messages"] if isinstance(m, ToolMessage)]


class HoopsPassAgent(ResponsesAgent):
    """Standard agent interface: AI Playground, Databricks Apps, Model Serving and mlflow.genai.evaluate understand it."""

    def __init__(self, workspace_client: WorkspaceClient | None = None):
        self.w = workspace_client or WorkspaceClient()
        self.servers = mcp_servers(self.w.config.host.rstrip("/"))
        self._agent = None

    async def _build(self):
        client = DatabricksMultiServerMCPClient([
            DatabricksMCPServer(name=label, url=url, workspace_client=self.w) for label, url in self.servers.items()])
        tools = await client.get_tools()
        return create_agent(
            # No temperature: Claude Sonnet 5.5 rejects the parameter (400 BAD_REQUEST).
            model=ChatDatabricks(endpoint=config.get("llm_endpoint")),
            tools=tools,
            system_prompt=config.get("system_prompt"),
        )

    @property
    def agent(self):
        if self._agent is None:
            with _BUILD_LOCK:   # parallel evaluation threads must not build the agent twice
                if self._agent is None:
                    self._agent = run_async(self._build())
        return self._agent

    @staticmethod
    def _messages(request: ResponsesAgentRequest) -> list:
        """Responses API input -> chat messages. The system prompt comes from create_agent, so drop system turns."""
        items = [i.model_dump() for i in request.input]
        return [{"role": i["role"], "content": i["content"]} for i in items
                if i.get("role") in ("user", "assistant") and isinstance(i.get("content"), str)]

    def ask(self, messages: list) -> dict:
        """Full LangGraph state for one request: all messages, including tool calls and results."""
        return run_async(self.agent.ainvoke({"messages": messages}, config={"recursion_limit": RECURSION_LIMIT}))

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        state = self.ask(self._messages(request))
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=str(state["messages"][-1].content), id=str(uuid4()))],
            custom_outputs={"tools": tools_used(state)},
        )

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        """MCP tools are async-only, so the answer is produced in one piece and streamed as a single item."""
        response = self.predict(request)
        for item in response.output:
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


mlflow.langchain.autolog()
mlflow.models.set_model(HoopsPassAgent())
