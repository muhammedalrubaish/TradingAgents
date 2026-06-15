"""TradingAgents Web UI — Streamlit application.

Launch with:
    streamlit run app.py
"""

from __future__ import annotations

import datetime
import os
import re
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import streamlit as st

# ── page config (must be first Streamlit call) ──────────────────────────────
st.set_page_config(
    page_title="TradingAgents",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.agent-card {
    background: #1e1e2e;
    border-radius: 10px;
    padding: 12px 16px;
    margin: 4px 0;
    border-left: 4px solid #555;
}
.agent-card.pending  { border-left-color: #f59e0b; }
.agent-card.running  { border-left-color: #3b82f6; }
.agent-card.done     { border-left-color: #22c55e; }
.status-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
}
.badge-pending  { background:#f59e0b22; color:#f59e0b; }
.badge-running  { background:#3b82f622; color:#3b82f6; }
.badge-done     { background:#22c55e22; color:#22c55e; }
.metric-box {
    background:#1e1e2e;
    border-radius:8px;
    padding:10px 14px;
    text-align:center;
}
</style>
""", unsafe_allow_html=True)

# ── constants ────────────────────────────────────────────────────────────────
ANALYSTS = ["market", "social", "news", "fundamentals"]
ANALYST_LABELS = {
    "market": "📊 Market Analyst",
    "social": "💬 Sentiment Analyst",
    "news": "📰 News Analyst",
    "fundamentals": "📋 Fundamentals Analyst",
}
FIXED_AGENTS = [
    "Bull Researcher", "Bear Researcher", "Research Manager",
    "Trader",
    "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst",
    "Portfolio Manager",
]
ANALYST_AGENT_MAP = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
LLM_PROVIDERS = [
    "openai", "anthropic", "google", "ollama",
    "deepseek", "openai_compatible",
]
PROVIDER_MODELS = {
    "openai":            (["gpt-5.5", "gpt-5.4", "gpt-4.1"], ["gpt-5.4-mini", "gpt-4.1-mini"]),
    "anthropic":         (["claude-opus-4-8", "claude-sonnet-4-6"], ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"]),
    "google":            (["gemini-2.5-pro", "gemini-2.0-flash"], ["gemini-2.0-flash", "gemini-2.5-flash"]),
    "ollama":            (["llama3.3:70b", "qwen3:32b"], ["llama3.3:8b", "qwen3:8b"]),
    "deepseek":          (["deepseek-reasoner", "deepseek-chat"], ["deepseek-chat", "deepseek-reasoner"]),
    "openai_compatible": (["custom-model"], ["custom-model"]),
}

# ── session state defaults ───────────────────────────────────────────────────
def _init_state():
    defaults = {
        "running": False,
        "done": False,
        "agent_status": {},
        "report_sections": {},
        "messages": [],
        "final_state": {},
        "error": None,
        "queue": None,
        "start_time": None,
        "elapsed": 0,
        "selected_analysts": ["market", "news", "fundamentals"],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/imgs/logo.png",
             use_container_width=True)  # falls back gracefully if 404
    st.title("TradingAgents")
    st.caption("Multi-Agent LLM Trading Framework")
    st.divider()

    ticker = st.text_input("📌 Ticker Symbol", value="NVDA",
                           placeholder="e.g. NVDA, BTC-USD, 0700.HK").upper().strip()

    max_date = datetime.date.today()
    analysis_date = st.date_input("📅 Analysis Date", value=max_date - datetime.timedelta(days=1),
                                  max_value=max_date)

    st.subheader("🤖 Analysts")
    selected_analysts = []
    for key, label in ANALYST_LABELS.items():
        checked = key in st.session_state["selected_analysts"]
        if st.checkbox(label, value=checked, key=f"analyst_{key}"):
            selected_analysts.append(key)
    st.session_state["selected_analysts"] = selected_analysts

    st.subheader("🔬 Research Depth")
    research_depth = st.select_slider("Debate Rounds", options=[1, 2, 3], value=1)

    st.subheader("🧠 LLM Provider")
    provider = st.selectbox("Provider", LLM_PROVIDERS, index=0)

    deep_models, quick_models = PROVIDER_MODELS.get(provider, (["custom"], ["custom"]))
    deep_model = st.selectbox("Deep thinker", deep_models)
    quick_model = st.selectbox("Quick thinker", quick_models)

    if provider == "openai_compatible":
        backend_url = st.text_input("Backend URL", placeholder="http://localhost:11434/v1")
    else:
        backend_url = ""

    output_lang = st.selectbox("🌐 Output Language",
                                ["English", "Arabic", "Chinese", "French", "Spanish", "German"])

    st.divider()
    run_btn = st.button("🚀 Start Analysis", type="primary",
                        disabled=st.session_state["running"],
                        use_container_width=True)

# ── main area ────────────────────────────────────────────────────────────────
st.header(f"📈 TradingAgents {'— ' + ticker if ticker else ''}")

tab_live, tab_report, tab_trade = st.tabs(["🔄 Live Progress", "📄 Full Report", "⚡ Trade Execution"])


# ── helpers ──────────────────────────────────────────────────────────────────
def _status_badge(status: str) -> str:
    cls = {"pending": "badge-pending", "in_progress": "badge-running", "completed": "badge-done"}.get(status, "badge-pending")
    icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}.get(status, "⏳")
    label = {"pending": "Pending", "in_progress": "Running", "completed": "Done"}.get(status, status)
    return f'<span class="status-badge {cls}">{icon} {label}</span>'


def _card_cls(status: str) -> str:
    return {"pending": "pending", "in_progress": "running", "completed": "done"}.get(status, "pending")


def _render_agent_table(agent_status: dict, selected_analysts: list):
    teams = {}
    analyst_agents = [ANALYST_AGENT_MAP[a] for a in selected_analysts if a in ANALYST_AGENT_MAP]
    if analyst_agents:
        teams["Analyst Team"] = analyst_agents
    teams["Research Team"] = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    teams["Trading Team"] = ["Trader"]
    teams["Risk Management"] = ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"]
    teams["Portfolio Management"] = ["Portfolio Manager"]

    html = ""
    for team, agents in teams.items():
        active = [a for a in agents if a in agent_status]
        if not active:
            continue
        html += f"<p style='color:#888;font-size:0.8rem;margin:8px 0 2px'>{team}</p>"
        for agent in active:
            status = agent_status.get(agent, "pending")
            cls = _card_cls(status)
            badge = _status_badge(status)
            html += f'<div class="agent-card {cls}"><b>{agent}</b> &nbsp; {badge}</div>'
    return html


def _build_config(provider, deep_model, quick_model, backend_url, research_depth, output_lang, selected_analysts):
    from tradingagents.default_config import DEFAULT_CONFIG
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = provider
    cfg["deep_think_llm"] = deep_model
    cfg["quick_think_llm"] = quick_model
    cfg["backend_url"] = backend_url or None
    cfg["max_debate_rounds"] = research_depth
    cfg["max_risk_discuss_rounds"] = research_depth
    cfg["output_language"] = output_lang
    return cfg


# ── background analysis thread ───────────────────────────────────────────────
def _run_analysis(queue: Queue, ticker: str, date_str: str, selected_analysts: list, config: dict):
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.analyst_execution import (
            build_analyst_execution_plan,
            get_initial_analyst_node,
            sync_analyst_tracker_from_chunk,
            AnalystWallTimeTracker,
        )
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        graph = TradingAgentsGraph(selected_analysts, config=config, debug=False)

        # Detect asset type
        from cli.utils import detect_asset_type
        asset_type = detect_asset_type(ticker).value

        instrument_context = graph.resolve_instrument_context(ticker, asset_type)
        init_state = graph.propagator.create_initial_state(
            ticker, date_str, asset_type=asset_type,
            instrument_context=instrument_context,
        )
        args = graph.propagator.get_graph_args()

        analyst_plan = build_analyst_execution_plan(selected_analysts, concurrency_limit=1)
        tracker = AnalystWallTimeTracker(analyst_plan)

        queue.put(("status", "started"))

        ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
        ANALYST_REPORT_MAP = {"market": "market_report", "social": "sentiment_report",
                               "news": "news_report", "fundamentals": "fundamentals_report"}

        report_sections = {k: None for k in ANALYST_REPORT_MAP.values()}
        report_sections.update({"investment_plan": None, "trader_investment_plan": None, "final_trade_decision": None})

        accumulated_reports = {}
        processed_ids = set()
        trace = []

        for chunk in graph.graph.stream(init_state, **args):
            trace.append(chunk)

            # Messages
            for msg in chunk.get("messages", []):
                mid = getattr(msg, "id", None)
                if mid:
                    if mid in processed_ids:
                        continue
                    processed_ids.add(mid)
                content = ""
                raw = getattr(msg, "content", None)
                if isinstance(raw, str):
                    content = raw.strip()
                elif isinstance(raw, list):
                    content = " ".join(
                        p.get("text", "") for p in raw if isinstance(p, dict) and p.get("type") == "text"
                    ).strip()
                if content:
                    mtype = "Agent" if isinstance(msg, AIMessage) else ("Data" if isinstance(msg, ToolMessage) else "User")
                    queue.put(("message", (mtype, content[:300])))

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc["name"] if isinstance(tc, dict) else tc.name
                        queue.put(("tool", name))

            sync_analyst_tracker_from_chunk(tracker, chunk)

            # Analyst report updates
            for analyst_key in ANALYST_ORDER:
                if analyst_key not in selected_analysts:
                    continue
                rep_key = ANALYST_REPORT_MAP[analyst_key]
                if chunk.get(rep_key):
                    accumulated_reports[rep_key] = chunk[rep_key]
                    queue.put(("report_section", (rep_key, chunk[rep_key])))
                    queue.put(("agent_status", (ANALYST_AGENT_MAP[analyst_key], "completed")))

            # Research team
            if chunk.get("investment_debate_state"):
                d = chunk["investment_debate_state"]
                if d.get("judge_decision", "").strip():
                    queue.put(("agent_status", ("Research Manager", "completed")))
                    queue.put(("agent_status", ("Trader", "in_progress")))
                    queue.put(("report_section", ("investment_plan", d["judge_decision"])))
                elif d.get("bull_history") or d.get("bear_history"):
                    queue.put(("agent_status", ("Bull Researcher", "in_progress")))

            # Trader
            if chunk.get("trader_investment_plan"):
                queue.put(("report_section", ("trader_investment_plan", chunk["trader_investment_plan"])))
                queue.put(("agent_status", ("Trader", "completed")))
                queue.put(("agent_status", ("Aggressive Analyst", "in_progress")))

            # Risk + Portfolio
            if chunk.get("risk_debate_state"):
                r = chunk["risk_debate_state"]
                if r.get("judge_decision", "").strip():
                    queue.put(("report_section", ("final_trade_decision", r["judge_decision"])))
                    for ag in ["Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager"]:
                        queue.put(("agent_status", (ag, "completed")))

        # Merge final state
        final = {}
        for c in trace:
            final.update(c)
        queue.put(("final_state", final))
        queue.put(("status", "done"))

    except Exception as exc:
        import traceback
        queue.put(("error", traceback.format_exc()))


# ── trigger analysis ─────────────────────────────────────────────────────────
if run_btn and not st.session_state["running"]:
    if not ticker:
        st.error("Please enter a ticker symbol.")
    elif not selected_analysts:
        st.error("Please select at least one analyst.")
    else:
        # Reset state
        all_agents = [ANALYST_AGENT_MAP[a] for a in selected_analysts if a in ANALYST_AGENT_MAP] + FIXED_AGENTS
        st.session_state.update({
            "running": True,
            "done": False,
            "agent_status": {a: "pending" for a in all_agents},
            "report_sections": {},
            "messages": [],
            "final_state": {},
            "error": None,
            "start_time": time.time(),
            "elapsed": 0,
        })
        q = Queue()
        st.session_state["queue"] = q
        cfg = _build_config(provider, deep_model, quick_model, backend_url,
                            research_depth, output_lang, selected_analysts)
        t = threading.Thread(
            target=_run_analysis,
            args=(q, ticker, analysis_date.strftime("%Y-%m-%d"), selected_analysts, cfg),
            daemon=True,
        )
        t.start()
        st.rerun()


# ── drain queue ───────────────────────────────────────────────────────────────
if st.session_state["running"] and st.session_state["queue"]:
    q: Queue = st.session_state["queue"]
    try:
        while True:
            event, data = q.get_nowait()
            if event == "status" and data == "done":
                st.session_state["running"] = False
                st.session_state["done"] = True
            elif event == "status" and data == "started":
                # mark first analyst in_progress
                if selected_analysts:
                    first = ANALYST_AGENT_MAP.get(selected_analysts[0])
                    if first:
                        st.session_state["agent_status"][first] = "in_progress"
            elif event == "agent_status":
                agent, status = data
                st.session_state["agent_status"][agent] = status
            elif event == "report_section":
                section, content = data
                st.session_state["report_sections"][section] = content
            elif event == "message":
                st.session_state["messages"].append(data)
                if len(st.session_state["messages"]) > 80:
                    st.session_state["messages"] = st.session_state["messages"][-80:]
            elif event == "tool":
                st.session_state["messages"].append(("Tool", f"🔧 {data}"))
                if len(st.session_state["messages"]) > 80:
                    st.session_state["messages"] = st.session_state["messages"][-80:]
            elif event == "final_state":
                st.session_state["final_state"] = data
            elif event == "error":
                st.session_state["error"] = data
                st.session_state["running"] = False
    except Empty:
        pass

    if st.session_state["start_time"]:
        st.session_state["elapsed"] = int(time.time() - st.session_state["start_time"])


# ── TAB 1: Live Progress ──────────────────────────────────────────────────────
with tab_live:
    if not st.session_state["running"] and not st.session_state["done"] and not st.session_state["error"]:
        st.info("Configure settings in the sidebar and click **🚀 Start Analysis** to begin.")

    if st.session_state["error"]:
        st.error("Analysis failed")
        with st.expander("Error details"):
            st.code(st.session_state["error"])

    if st.session_state["running"] or st.session_state["done"]:
        elapsed = st.session_state["elapsed"]
        agents_done = sum(1 for s in st.session_state["agent_status"].values() if s == "completed")
        agents_total = len(st.session_state["agent_status"])
        reports_done = sum(1 for v in st.session_state["report_sections"].values() if v)

        # Metrics row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("⏱ Elapsed", f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        m2.metric("🤖 Agents", f"{agents_done}/{agents_total}")
        m3.metric("📄 Reports", reports_done)
        m4.metric("💬 Messages", len(st.session_state["messages"]))

        if st.session_state["running"]:
            pct = agents_done / max(agents_total, 1)
            st.progress(pct, text=f"Running… {int(pct*100)}%")

        if st.session_state["done"]:
            st.success("✅ Analysis complete!")

        col_agents, col_msgs = st.columns([2, 3])

        with col_agents:
            st.subheader("Agent Status")
            html = _render_agent_table(
                st.session_state["agent_status"],
                st.session_state.get("selected_analysts", selected_analysts),
            )
            st.markdown(html, unsafe_allow_html=True)

        with col_msgs:
            st.subheader("Live Messages")
            msgs = st.session_state["messages"][-20:]
            for mtype, content in reversed(msgs):
                icon = {"Agent": "🤖", "Data": "📦", "User": "👤", "Tool": "🔧"}.get(mtype, "💬")
                color = {"Agent": "#3b82f6", "Data": "#8b5cf6", "Tool": "#f59e0b"}.get(mtype, "#6b7280")
                st.markdown(
                    f'<div style="border-left:3px solid {color};padding:4px 10px;margin:3px 0;'
                    f'font-size:0.82rem;background:#1e1e2e;border-radius:0 6px 6px 0">'
                    f'{icon} <b style="color:{color}">{mtype}</b> — {content}</div>',
                    unsafe_allow_html=True,
                )

        # Latest report snippet
        reps = st.session_state["report_sections"]
        latest_content = None
        latest_label = ""
        SECTION_LABELS = {
            "market_report": "📊 Market Analysis",
            "sentiment_report": "💬 Sentiment Analysis",
            "news_report": "📰 News Analysis",
            "fundamentals_report": "📋 Fundamentals Analysis",
            "investment_plan": "🔬 Research Team Decision",
            "trader_investment_plan": "💼 Trader Plan",
            "final_trade_decision": "🏦 Portfolio Manager Decision",
        }
        for key in SECTION_LABELS:
            if reps.get(key):
                latest_label = SECTION_LABELS[key]
                latest_content = reps[key]
        if latest_content:
            st.divider()
            st.subheader(f"Latest Report: {latest_label}")
            st.markdown(latest_content[:3000] + ("…" if len(latest_content) > 3000 else ""))

    # Auto-refresh while running
    if st.session_state["running"]:
        time.sleep(1.5)
        st.rerun()


# ── TAB 2: Full Report ────────────────────────────────────────────────────────
with tab_report:
    reps = st.session_state["report_sections"]
    fs = st.session_state["final_state"]

    if not reps and not fs:
        st.info("The full report will appear here after analysis completes.")
    else:
        SECTIONS = [
            ("market_report",           "📊 Market Analysis"),
            ("sentiment_report",        "💬 Sentiment Analysis"),
            ("news_report",             "📰 News Analysis"),
            ("fundamentals_report",     "📋 Fundamentals Analysis"),
            ("investment_plan",         "🔬 Research Team Decision"),
            ("trader_investment_plan",  "💼 Trader Plan"),
            ("final_trade_decision",    "🏦 Portfolio Manager Decision"),
        ]
        for key, label in SECTIONS:
            content = reps.get(key) or fs.get(key)
            if content:
                with st.expander(label, expanded=(key == "final_trade_decision")):
                    st.markdown(content)

        # Download button
        if reps:
            md_parts = [f"# Trading Analysis Report: {ticker}\n\nDate: {analysis_date}\n"]
            for key, label in SECTIONS:
                c = reps.get(key) or fs.get(key)
                if c:
                    md_parts.append(f"## {label}\n\n{c}")
            full_md = "\n\n---\n\n".join(md_parts)
            st.download_button(
                "⬇️ Download Full Report (Markdown)",
                data=full_md.encode(),
                file_name=f"{ticker}_{analysis_date}.md",
                mime="text/markdown",
            )


# ── TAB 3: Trade Execution (Alpaca) ──────────────────────────────────────────
with tab_trade:
    st.subheader("⚡ Execute via Alpaca Markets")

    # Check alpaca-py
    try:
        import alpaca  # noqa: F401
        alpaca_installed = True
    except ImportError:
        alpaca_installed = False

    if not alpaca_installed:
        st.warning("alpaca-py is not installed. Run: `pip install alpaca-py`")
        st.stop()

    col_creds, col_exec = st.columns([1, 2])

    with col_creds:
        st.subheader("🔑 Credentials")
        api_key = st.text_input("ALPACA_API_KEY",
                                value=os.environ.get("ALPACA_API_KEY", ""),
                                type="password")
        secret_key = st.text_input("ALPACA_SECRET_KEY",
                                   value=os.environ.get("ALPACA_SECRET_KEY", ""),
                                   type="password")
        paper_mode = st.toggle("Paper Trading Mode", value=True)

        if st.button("🔗 Connect & Check Account"):
            if api_key and secret_key:
                os.environ["ALPACA_API_KEY"] = api_key
                os.environ["ALPACA_SECRET_KEY"] = secret_key
                os.environ["ALPACA_PAPER"] = "true" if paper_mode else "false"
                try:
                    from tradingagents.execution.alpaca import get_account_info, get_position
                    acct = get_account_info()
                    st.success(f"Connected ({'Paper' if acct['paper'] else 'LIVE'})")
                    st.metric("Buying Power", f"${acct['buying_power']:,.2f}")
                    st.metric("Portfolio Value", f"${acct['portfolio_value']:,.2f}")
                    st.metric("Cash", f"${acct['cash']:,.2f}")
                    if ticker:
                        pos = get_position(ticker)
                        if pos:
                            st.info(f"Open position in {ticker}: {pos['qty']} shares | P&L ${pos['unrealized_pl']:,.2f}")
                        else:
                            st.info(f"No open position in {ticker}")
                except Exception as exc:
                    st.error(f"Connection failed: {exc}")
            else:
                st.error("Enter API key and secret key.")

    with col_exec:
        st.subheader("📋 Decision Summary")

        # Parse rating from final decision
        reps = st.session_state["report_sections"]
        fs = st.session_state["final_state"]
        decision_text = reps.get("final_trade_decision") or fs.get("final_trade_decision") or ""

        if not decision_text:
            rd = fs.get("risk_debate_state") or {}
            decision_text = rd.get("judge_decision", "") or ""

        from tradingagents.agents.schemas import PortfolioRating
        parsed_rating = None
        if decision_text:
            m = re.search(r"\*{0,2}Rating\*{0,2}[:\s]+([A-Za-z]+)", decision_text, re.IGNORECASE)
            if m:
                try:
                    parsed_rating = PortfolioRating(m.group(1).strip().capitalize())
                except ValueError:
                    pass

        if not decision_text:
            st.info("Run an analysis first to get a trade decision.")
        else:
            rating_colors = {
                PortfolioRating.BUY: "🟢",
                PortfolioRating.OVERWEIGHT: "🟩",
                PortfolioRating.HOLD: "🟡",
                PortfolioRating.UNDERWEIGHT: "🟧",
                PortfolioRating.SELL: "🔴",
            }
            if parsed_rating:
                icon = rating_colors.get(parsed_rating, "⚪")
                st.markdown(f"### {icon} Rating: **{parsed_rating.value}**")
                st.markdown(f"**Ticker:** {ticker} | **Date:** {analysis_date}")

            with st.expander("View Decision Text"):
                st.markdown(decision_text[:2000])

            st.divider()
            st.subheader("🎯 Place Order")

            order_type = st.radio("Order Type", ["Market Order", "Limit Order"], horizontal=True)
            notional = st.number_input("Dollar Amount ($)", min_value=1.0, value=1000.0, step=100.0)
            limit_price = None
            if order_type == "Limit Order":
                limit_price = st.number_input("Limit Price ($)", min_value=0.01, value=100.0, step=0.01)

            if parsed_rating in (PortfolioRating.BUY, PortfolioRating.OVERWEIGHT):
                side_label, side_color = "BUY", "green"
            elif parsed_rating in (PortfolioRating.SELL, PortfolioRating.UNDERWEIGHT):
                side_label, side_color = "SELL", "red"
            else:
                side_label, side_color = "HOLD", "orange"

            if parsed_rating and parsed_rating != PortfolioRating.HOLD:
                order_desc = f"**{side_label}** ${notional:,.0f} of **{ticker}**"
                if limit_price:
                    order_desc += f" @ ${limit_price:.2f} (limit)"
                else:
                    order_desc += " (market)"
                st.markdown(f":{side_color}[{order_desc}]")

                execute_btn = st.button(
                    f"{'🟢' if side_label == 'BUY' else '🔴'} Execute {side_label} Order",
                    type="primary",
                )
                if execute_btn:
                    if not api_key or not secret_key:
                        st.error("Set API credentials and connect first.")
                    else:
                        os.environ["ALPACA_API_KEY"] = api_key
                        os.environ["ALPACA_SECRET_KEY"] = secret_key
                        os.environ["ALPACA_PAPER"] = "true" if paper_mode else "false"
                        from tradingagents.execution.alpaca import execute_from_portfolio_decision
                        with st.spinner("Submitting order to Alpaca…"):
                            result = execute_from_portfolio_decision(
                                symbol=ticker,
                                rating=parsed_rating,
                                notional=notional,
                                limit_price=limit_price,
                            )
                        if result.success:
                            st.success(f"✅ Order submitted!")
                            res_col1, res_col2, res_col3 = st.columns(3)
                            res_col1.metric("Order ID", result.order_id or "N/A")
                            res_col2.metric("Status", result.status)
                            res_col3.metric("Side", result.side.upper() if result.side else "N/A")
                            if result.filled_price:
                                st.metric("Fill Price", f"${result.filled_price:,.4f}")
                        else:
                            st.error(f"❌ Order failed: {result.message}")
            elif parsed_rating == PortfolioRating.HOLD:
                st.warning("⚠️ Decision is HOLD — no order will be placed.")
            else:
                st.info("Complete an analysis to enable order execution.")
