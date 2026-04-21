#!/usr/bin/env python3
"""
Mini-Agent HTTP API Service
Wraps Mini-Agent with a FastAPI HTTP interface for web integration.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add mini_agent to path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from mini_agent.agent import Agent
from mini_agent.config import Config, AgentConfig, LLMConfig, ToolsConfig, RetryConfig
from mini_agent.llm import LLMClient
from mini_agent.retry import RetryConfig as RetryConfigBase
from mini_agent.schema import Message
from mini_agent.tools import (
    BashTool,
    ReadTool,
    WriteTool,
    EditTool,
    SessionNoteTool,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mini-agent-service")


# ─── Pydantic Models ────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    thinking: Optional[str] = None
    tool_calls: list = []


class ToolCallResult(BaseModel):
    name: str
    args: dict
    result: str
    success: bool


# ─── Session Store ────────────────────────────────────────────────────────────

class SessionStore:
    """Manages per-session Mini-Agent instances."""

    def __init__(self, config: Config):
        self.config = config
        self.sessions: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _create_agent(self, workspace_dir: Path) -> Agent:
        """Create a new Mini-Agent instance."""
        rcfg = self.config.llm.retry
        llm = LLMClient(
            api_key=self.config.llm.api_key,
            provider=self.config.llm.provider,
            api_base=self.config.llm.api_base,
            model=self.config.llm.model,
            retry_config=RetryConfigBase(
                enabled=rcfg.enabled,
                max_retries=rcfg.max_retries,
                initial_delay=rcfg.initial_delay,
                max_delay=rcfg.max_delay,
                exponential_base=rcfg.exponential_base,
            ),
        )

        tools = [
            BashTool(),
            ReadTool(),
            WriteTool(),
            EditTool(),
            SessionNoteTool(),
        ]

        system_prompt_path = self.config.agent.system_prompt_path
        if system_prompt_path and Path(system_prompt_path).exists():
            system_prompt = Path(system_prompt_path).read_text(encoding="utf-8")
        else:
            system_prompt = (
                "You are a helpful AI assistant powered by MiniMax M2.7. "
                "You have access to terminal (bash), file read/write/edit, and note tools. "
                "Be concise and helpful."
            )

        workspace_dir.mkdir(parents=True, exist_ok=True)
        return Agent(
            llm_client=llm,
            system_prompt=system_prompt,
            tools=tools,
            max_steps=self.config.agent.max_steps,
            workspace_dir=str(workspace_dir),
        )

    async def get_or_create_session(self, session_id: str | None) -> tuple[str, Agent]:
        """Get existing session or create new one."""
        async with self._lock:
            if session_id and session_id in self.sessions:
                return session_id, self.sessions[session_id]["agent"]

            new_id = session_id or f"session-{len(self.sessions) + 1}"
            workspace = Path(self.config.agent.workspace_dir) / new_id
            agent = self._create_agent(workspace)
            self.sessions[new_id] = {"agent": agent, "workspace": workspace}
            logger.info(f"Created new session: {new_id}")
            return new_id, agent

    async def run_agent(self, agent: Agent, message: str) -> tuple[str, Optional[str], list]:
        """Run agent with a user message, return (response, thinking, tool_calls)."""
        agent.add_user_message(message)

        thinking_output = None
        tool_calls = []

        for step_num in range(agent.max_steps):
            tool_schemas = [tool.to_schema() for tool in agent.tools.values()]
            try:
                response = await agent.llm.generate(
                    messages=agent.messages,
                    tools=tool_schemas,
                )
            except Exception as exc:
                logger.exception("LLM error")
                return f"Error: {exc}", None, []

            if response.thinking:
                thinking_output = response.thinking

            if response.content:
                await asyncio.sleep(0)  # yield to event loop

            agent.messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    thinking=response.thinking,
                    tool_calls=response.tool_calls,
                )
            )

            if not response.tool_calls:
                return response.content or "", thinking_output, tool_calls

            for call in response.tool_calls:
                name, args = call.function.name, call.function.arguments
                logger.info(f"[Step {step_num+1}] Tool: {name}")

                tool = agent.tools.get(name)
                if not tool:
                    text = f"[ERROR] Unknown tool: {name}"
                    success = False
                else:
                    try:
                        result = await tool.execute(**args)
                        text = result.content if result.success else f"[ERROR] {result.error}"
                        success = result.success
                    except Exception as exc:
                        text = f"[ERROR] Tool error: {exc}"
                        success = False

                tool_calls.append({
                    "name": name,
                    "args": args,
                    "result": text[:500],  # truncate long output
                    "success": success,
                })

                agent.messages.append(
                    Message(role="tool", content=text, tool_call_id=call.id, name=name)
                )

        return "[MAX STEPS REACHED] Agent reached maximum steps.", thinking_output, tool_calls


# ─── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(title="Mini-Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global config and session store (initialized on startup)
config: Config = None
session_store: SessionStore = None


@app.on_event("startup")
async def startup():
    global config, session_store
    config_path = Path(__file__).parent / "config.yaml"
    # Allow API key override via env var
    import yaml
    with open(config_path) as f:
        cfg_data = yaml.safe_load(f)
    api_key = os.environ.get("MINIMAX_API_KEY") or cfg_data.get("api_key", "")
    if not api_key or api_key == "your-minimax-api-key-here":
        raise RuntimeError("Set MINIMAX_API_KEY env var or update config.yaml")
    cfg_data["api_key"] = api_key
    from dataclasses import replace
    from mini_agent.retry import RetryConfig as MiniRetryConfig
    rcfg_d = cfg_data.get("retry", {})
    rcfg = RetryConfig(
        enabled=rcfg_d.get("enabled", True),
        max_retries=rcfg_d.get("max_retries", 3),
        initial_delay=rcfg_d.get("initial_delay", 1.0),
        max_delay=rcfg_d.get("max_delay", 60.0),
        exponential_base=rcfg_d.get("exponential_base", 2.0),
    )
    llm_d = cfg_data
    llm_cfg = LLMConfig(
        api_key=api_key,
        api_base=llm_d.get("api_base", "https://api.minimaxi.com"),
        model=llm_d.get("model", "MiniMax-M2.7"),
        provider=llm_d.get("provider", "anthropic"),
        retry=rcfg,
    )
    agent_d = cfg_data
    agent_cfg = AgentConfig(
        max_steps=agent_d.get("max_steps", 50),
        workspace_dir=agent_d.get("workspace_dir", "./workspace"),
        system_prompt_path=agent_d.get("system_prompt_path", "system_prompt.md"),
    )
    tools_d = cfg_data.get("tools", {})
    tools_cfg = ToolsConfig(
        enable_file_tools=tools_d.get("enable_file_tools", True),
        enable_bash=tools_d.get("enable_bash", True),
        enable_note=tools_d.get("enable_note", True),
        enable_skills=tools_d.get("enable_skills", False),
        enable_mcp=tools_d.get("enable_mcp", False),
    )
    config = Config(llm=llm_cfg, agent=agent_cfg, tools=tools_cfg)
    session_store = SessionStore(config)
    logger.info(f"Mini-Agent service started. Model: {config.llm.model}")


@app.get("/")
async def root():
    return {
        "service": "Mini-Agent API",
        "model": config.llm.model if config else "unknown",
        "endpoints": ["/chat", "/sessions", "/health"],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint — runs Mini-Agent with the user message."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    session_id, agent = await session_store.get_or_create_session(req.session_id)

    response, thinking, tool_calls = await session_store.run_agent(agent, req.message)

    return ChatResponse(
        response=response,
        session_id=session_id,
        thinking=thinking,
        tool_calls=tool_calls,
    )


@app.get("/sessions")
async def list_sessions():
    """List all active sessions."""
    return {
        "sessions": [
            {"id": sid, "workspace": str(sess["workspace"])}
            for sid, sess in session_store.sessions.items()
        ]
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    async with session_store._lock:
        if session_id in session_store.sessions:
            del session_store.sessions[session_id]
            return {"deleted": session_id}
    raise HTTPException(status_code=404, detail="Session not found")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
