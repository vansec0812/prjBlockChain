import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  Database,
  Download,
  Filter,
  Layers3,
  LogOut,
  Network,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, query } from "./api";
import { formatMoney } from "./money";
import type { GraphProps } from "./Graph";
const GraphRenderer = lazy(() =>
  import("./Graph").then((module) => ({ default: module.Graph })),
);
function Graph(props: GraphProps) {
  return (
    <Suspense
      fallback={<div className="graph-loading">Đang tải trình hiển thị…</div>}
    >
      <GraphRenderer {...props} />
    </Suspense>
  );
}
import type {
  Alert,
  Config,
  GraphData,
  GraphParams,
  Metrics,
  Run,
  Tx,
  User,
} from "./types";

const patterns = [
  "STRUCTURING",
  "SMURFING",
  "PEELING",
  "MIXING_PATTERN",
  "RAPID_HIGH_RISK",
  "CIRCULAR_FLOW",
];
const names: Record<string, string> = {
  STRUCTURING: "Chia nhỏ giao dịch",
  SMURFING: "Phân tán & hội tụ",
  PEELING: "Chuỗi peeling",
  MIXING_PATTERN: "Mẫu mixing",
  RAPID_HIGH_RISK: "Luân chuyển rủi ro",
  DIRECT_BLACKLIST: "Khớp blacklist",
  CIRCULAR_FLOW: "Chu trình vòng lặp",
};
const short = (value: string) =>
  value.length > 22 ? value.slice(0, 10) + "…" + value.slice(-6) : value;
const percent = (value: number | null | undefined) =>
  value == null ? "—" : (value * 100).toFixed(1) + "%";
const numeric = (value: number | undefined, decimals = 0) =>
  value == null
    ? "—"
    : value.toLocaleString("vi-VN", { maximumFractionDigits: decimals });
function Badge({ value }: { value: string }) {
  return (
    <span className={"badge " + value.toLowerCase()}>
      {(
        {
          completed: "Hoàn thành",
          queued: "Đang đợi",
          running: "Đang chạy",
          failed: "Thất bại",
          open: "Mới",
          in_review: "Đang xem xét",
          closed: "Đã đóng",
        } as Record<string, string>
      )[value] ?? value}
    </span>
  );
}

export function App() {
  const [user, setUser] = useState<User | null>(null),
    [initializing, setInitializing] = useState(true);
  const [username, setUsername] = useState("admin"),
    [password, setPassword] = useState("");
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [connected, setConnected] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]),
    [selected, setSelected] = useState<string | null>(
      new URLSearchParams(location.search).get("run"),
    ),
    [run, setRun] = useState<Run | null>(null);
  const [tab, setTab] = useState<"simulation" | "graph" | "alerts">(
    "simulation",
  );
  const [metrics, setMetrics] = useState<Metrics | null>(null),
    [alerts, setAlerts] = useState<Alert[]>([]),
    [txs, setTxs] = useState<Tx[]>([]),
    [cursor, setCursor] = useState<string | null>(null);
  const [config, setConfig] = useState<Config>({
    seed: 42,
    wallet_count: 500,
    transaction_count: 5000,
    simulation_days: 7,
    block_interval_seconds: 12,
    max_transactions_per_block: 100,
    patterns: [...patterns],
  });
  const [graph, setGraph] = useState<GraphData | null>(null),
    [graphParams, setGraphParams] = useState<GraphParams>({ hops: 2 }),
    [graphTick, setGraphTick] = useState(0),
    [graphLoading, setGraphLoading] = useState(false);
  const [center, setCenter] = useState(""),
    [hops, setHops] = useState(2),
    [cutoff, setCutoff] = useState<number | null>(null);
  const [detail, setDetail] = useState<Alert | null>(null),
    [wallet, setWallet] = useState<{
      address: string;
      balance_base: string;
      inflow_base: string;
      outflow_with_fees_base: string;
    } | null>(null),
    [edge, setEdge] = useState<Tx | null>(null);
  const [reviewComment, setReviewComment] = useState(""),
    [reviewStatus, setReviewStatus] = useState("in_review");
  const [statusFilter, setStatusFilter] = useState(""),
    [feedOpen, setFeedOpen] = useState(false),
    [feedJSON, setFeedJSON] = useState("");
  const createKey = useRef(crypto.randomUUID());
  const refreshRef = useRef<() => void>(() => {});
  const runStatusRef = useRef<string | undefined>(undefined);
  runStatusRef.current = run?.status;
  useEffect(() => {
    api<{ user: User | null }>("/session")
      .then((r) => setUser(r.user))
      .catch((e) => setError(String(e.message)))
      .finally(() => setInitializing(false));
  }, []);
  useEffect(() => {
    createKey.current = crypto.randomUUID();
  }, [config]);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const list = await api<Run[]>(
          "/simulations",
          "GET",
          undefined,
          controller.signal,
        );
        setRuns(list);
        if (!selected) {
          if (list.length) setSelected(list[0].id);
          return;
        }
        const [r, m, a, t] = await Promise.all([
          api<Run>(
            "/simulations/" + selected,
            "GET",
            undefined,
            controller.signal,
          ),
          api<Metrics>(
            "/metrics?" + query({ run_id: selected }),
            "GET",
            undefined,
            controller.signal,
          ),
          api<{ items: Alert[] }>(
            "/alerts?" +
              query({ run_id: selected, status: statusFilter || undefined }),
            "GET",
            undefined,
            controller.signal,
          ),
          api<{ items: Tx[]; next_cursor: string | null }>(
            "/transactions?" + query({ run_id: selected, limit: 20 }),
            "GET",
            undefined,
            controller.signal,
          ),
        ]);
        setRun(r);
        setMetrics(m);
        setAlerts(a.items);
        setTxs(t.items);
        setCursor(t.next_cursor);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      }
    };
    refreshRef.current = () => void refresh();
    void refresh();
    const poll = setInterval(() => {
      if (runStatusRef.current !== "completed") void refresh();
    }, 3000);
    return () => {
      controller.abort();
      clearInterval(poll);
    };
  }, [user, selected, statusFilter]);
  useEffect(() => {
    if (!user) return;
    const stream = new EventSource("/api/events");
    let timer: ReturnType<typeof setTimeout> | undefined;
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    const update = () => {
      clearTimeout(timer);
      timer = setTimeout(() => refreshRef.current(), 200);
    };
    for (const event of ["progress", "job", "alert", "resync"])
      stream.addEventListener(event, update);
    return () => {
      clearTimeout(timer);
      stream.close();
    };
  }, [user]);
  useEffect(() => {
    setDetail(null);
    setWallet(null);
    setEdge(null);
    setGraph(null);
    setGraphParams({ hops: 2 });
    setCutoff(null);
    setCenter("");
  }, [selected]);
  useEffect(() => {
    if (!selected || !user) return;
    const controller = new AbortController();
    setGraphLoading(true);
    api<GraphData>(
      "/graph?" +
        query({
          run_id: selected,
          max_nodes: 300,
          max_edges: 600,
          ...graphParams,
        }),
      "GET",
      undefined,
      controller.signal,
    )
      .then(setGraph)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setGraphLoading(false);
      });
    return () => controller.abort();
  }, [selected, user, graphParams, graphTick, run?.status === "completed"]);
  const act = async (operation: () => Promise<void>) => {
    setError("");
    setBusy(true);
    try {
      await operation();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const chooseRun = (id: string) => {
    setSelected(id);
    setRun(null);
    setMetrics(null);
    setAlerts([]);
    setTxs([]);
    const url = new URL(location.href);
    url.searchParams.set("run", id);
    history.replaceState(null, "", url);
  };
  const openAlert = async (alert: Alert) => {
    if (!selected) return;
    await act(async () => {
      const value = await api<Alert>(
        `/alerts/${alert.id}?${query({ run_id: selected })}`,
      );
      setDetail(value);
      setWallet(null);
      setEdge(null);
      setReviewComment("");
      setReviewStatus(value.status);
      setTab("graph");
      setGraphParams({
        alert_id: value.id,
        as_of_index:
          value.last_detected_order ??
          Math.max(value.detected_order, (run?.transaction_count ?? 1) - 1),
        analysis_version: value.analysis_version,
      });
    });
  };
  const onNode = useCallback(
    (address: string) => {
      if (!selected) return;
      api<typeof wallet>(
        "/wallets/" +
          address +
          "?" +
          query({ run_id: selected, as_of_index: graph?.as_of_index }),
      )
        .then((value) => {
          setWallet(value);
          setEdge(null);
        })
        .catch((e) => setError(e.message));
    },
    [selected, graph?.as_of_index],
  );
  const onEdge = useCallback(
    (id: string) => {
      const value =
        detail?.transactions?.find((t) => t.tx_hash === id) ??
        txs.find((t) => t.tx_hash === id);
      if (value) {
        setEdge(value);
        setWallet(null);
      } else if (graph) {
        const e = graph.edges.find((e) => e.id === id);
        if (e)
          setEdge({
            tx_hash: e.id,
            from_address: e.source,
            to_address: e.target,
            value_base: e.value_base,
            fee_base: e.fee_base,
            timestamp: e.timestamp,
            global_index: e.global_index,
            block_height: e.block_height,
            tx_index: e.tx_index,
            assessment: null,
          });
      }
    },
    [graph, detail, txs],
  );
  const canAdmin = user?.role === "Admin";
  const countAlerts = Object.values(metrics?.alerts_by_detector ?? {}).reduce(
    (a, b) => a + b,
    0,
  );
  const progress = run
    ? Math.round((run.analyzed_count / run.config.transaction_count) * 100)
    : 0;

  if (initializing)
    return (
      <div className="boot">
        <ShieldCheck size={36} />
        <p>Đang kết nối AML Observatory…</p>
      </div>
    );
  if (!user)
    return (
      <div className="login-shell">
        <div className="login-story">
          <div className="wordmark">
            <ShieldCheck size={25} /> AML Observatory
          </div>
          <span className="eyebrow">Phòng mô phỏng blockchain</span>
          <h1>
            Đọc dòng tiền.
            <br />
            Hiểu dấu hiệu.
          </h1>
          <p>
            Mô phỏng giao dịch blockchain và khám phá bằng chứng của các mẫu
            hành vi AML.
          </p>
          <small>Dữ liệu tổng hợp · Tài sản SIM · Baseline B2</small>
        </div>
        <form
          className="login-form"
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              const result = await api<{ user: User }>("/login", "POST", {
                username,
                password,
              });
              setUser(result.user);
              setPassword("");
            });
          }}
        >
          <span className="eyebrow">Workspace của bạn</span>
          <h2>Đăng nhập phòng mô phỏng</h2>
          <p>
            Tài khoản local: admin, analyst hoặc viewer.
            <br />
            Mật khẩu được tạo riêng trong file <code>.env</code>.
          </p>
          <label>
            Tên đăng nhập
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label>
            Mật khẩu
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <button className="primary" disabled={busy}>
            {busy ? "Đang xác thực…" : "Vào workspace"}
            <ArrowRight size={17} />
          </button>
          <small>
            Score thể hiện mức ưu tiên xem xét, không phải xác suất phạm tội.
          </small>
        </form>
      </div>
    );

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        Đến nội dung chính
      </a>
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <ShieldCheck size={23} />
          </div>
          <div>AML Observatory</div>
        </div>
        <nav aria-label="Điều hướng chính">
          {[
            {
              id: "simulation",
              label: "Điều khiển mô phỏng",
              shortLabel: "Mô phỏng",
            },
            {
              id: "graph",
              label: "Khám phá dòng tiền",
              shortLabel: "Dòng tiền",
            },
            {
              id: "alerts",
              label: "Cảnh báo & đánh giá",
              shortLabel: "Cảnh báo",
            },
          ].map((n) => (
            <button
              key={n.id}
              className={tab === n.id ? "active" : ""}
              aria-label={n.label}
              aria-current={tab === n.id ? "page" : undefined}
              onClick={() => setTab(n.id as typeof tab)}
            >
              {n.shortLabel}
              {n.id === "alerts" && countAlerts > 0 && (
                <span className="nav-count">{countAlerts}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="account-menu">
          <div>
            <b>{user.username}</b>
            <span>{user.role}</span>
          </div>
          <button
            className="icon-button"
            aria-label="Đăng xuất"
            onClick={() =>
              void act(async () => {
                await api("/logout", "POST");
                setUser(null);
                setSelected(null);
              })
            }
          >
            <LogOut size={17} />
          </button>
        </div>
      </header>
      <main id="workspace" tabIndex={-1}>
        <header className="topbar">
          <div className="workspace-context">
            <span>Local workspace</span>
            <span>SIM · Baseline B2</span>
          </div>
          <div className="topbar-right">
            <span className="connection">
              <i className={connected ? "live-dot" : "offline-dot"} />
              {connected ? "Cập nhật trực tiếp" : "Đang kết nối lại"}
            </span>
            {canAdmin && (
              <button
                className="plain"
                onClick={() => {
                  setFeedJSON(
                    JSON.stringify(
                      {
                        network_id: "sim-account-1",
                        address: graph?.nodes[0]?.id ?? "sim_0000000000000000",
                        category: "high_risk",
                        severity: 80,
                        confidence: 0.9,
                        source_name: "Local synthetic feed",
                        source_ref: "synthetic://analyst",
                        known_at: new Date().toISOString(),
                        valid_from: "2026-09-08T00:00:00Z",
                        valid_to: null,
                        list_version: "analyst-" + Date.now(),
                        enabled: true,
                      },
                      null,
                      2,
                    ),
                  );
                  setFeedOpen(true);
                }}
              >
                <Database size={16} />
                Nguồn TI
              </button>
            )}
          </div>
        </header>
        <div className="page">
          <div className="page-heading">
            <div>
              <h1>
                {tab === "simulation"
                  ? "Điều khiển mô phỏng"
                  : tab === "graph"
                    ? "Khám phá dòng tiền"
                    : "Cảnh báo & đánh giá"}
              </h1>
              <p>
                {tab === "simulation"
                  ? "Tạo dữ liệu có thể tái lập. Theo dõi sổ cái và các tín hiệu AML."
                  : tab === "graph"
                    ? "Đi từ cảnh báo đến giao dịch, nguồn và bằng chứng cụ thể."
                    : "Đánh giá tín hiệu và đo chất lượng trên dữ liệu có nhãn độc lập."}
              </p>
            </div>
            <div className="run-picker">
              <label htmlFor="run-picker">Lần mô phỏng</label>
              <select
                id="run-picker"
                value={selected ?? ""}
                onChange={(e) => chooseRun(e.target.value)}
              >
                <option value="" disabled>
                  Chọn hoặc tạo lần chạy
                </option>
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    Seed {r.seed} · {r.id.slice(0, 8)} · {r.status}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
              <button
                className="icon-button"
                aria-label="Đóng thông báo"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div className="metric-grid">
            <div className="metric-card">
              <div>
                <span>Giao dịch trong sổ cái</span>
              </div>
              <strong>{numeric(run?.transaction_count)}</strong>
              <small>
                {run
                  ? numeric(run.config.wallet_count) + " ví · SIM / 6 decimals"
                  : "Chưa có lần mô phỏng"}
              </small>
            </div>
            <div className="metric-card">
              <div>
                <span>Cảnh báo AML</span>
              </div>
              <strong>
                {metrics?.status === "pending" ? "—" : numeric(countAlerts)}
              </strong>
              <small>
                {metrics?.alerts_per_1000_tx != null
                  ? numeric(metrics.alerts_per_1000_tx, 2) +
                    " cảnh báo / 1.000 giao dịch"
                  : "Đợi phân tích hoàn tất"}
              </small>
            </div>
            <div className="metric-card">
              <div>
                <span>Tiến độ phân tích</span>
              </div>
              <strong>{run ? progress + "%" : "—"}</strong>
              <small>
                {run ? (
                  <>
                    <Badge value={run.status} /> {numeric(run.analyzed_count)}{" "}
                    đã đánh giá
                  </>
                ) : (
                  "Worker độc lập với API"
                )}
              </small>
            </div>
            <div className="metric-card">
              <div>
                <span>Chiến dịch được phát hiện</span>
              </div>
              <strong>
                {metrics?.campaign_count != null
                  ? `${metrics.campaigns_detected} / ${metrics.campaign_count}`
                  : "—"}
              </strong>
              <small>Khớp ít nhất 50% giao dịch lõi</small>
            </div>
          </div>

          {tab === "simulation" && (
            <>
              <section className="panel simulation-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Cấu hình lần chạy</h2>
                    <p>Cùng seed và cấu hình tạo cùng nội dung sổ cái.</p>
                  </div>
                  <span className="subtle-tag">ACCOUNT-BASED · SIM</span>
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      const result = await api<{ run_id: string }>(
                        "/simulations",
                        "POST",
                        config,
                        undefined,
                        { "Idempotency-Key": createKey.current },
                      );
                      createKey.current = crypto.randomUUID();
                      chooseRun(result.run_id);
                      refreshRef.current();
                    });
                  }}
                >
                  <div className="config-grid">
                    {[
                      {
                        key: "seed",
                        label: "Random seed",
                        min: 0,
                        max: 2147483647,
                      },
                      {
                        key: "wallet_count",
                        label: "Số lượng ví",
                        min: 80,
                        max: 10000,
                      },
                      {
                        key: "transaction_count",
                        label: "Số giao dịch",
                        min: 150,
                        max: 100000,
                      },
                      {
                        key: "simulation_days",
                        label: "Thời gian mô phỏng (ngày)",
                        min: 1,
                        max: 30,
                      },
                    ].map((f) => (
                      <label key={f.key}>
                        {f.label}
                        <input
                          type="number"
                          min={f.min}
                          max={f.max}
                          value={config[f.key as keyof Config] as number}
                          onChange={(e) =>
                            setConfig((c) => ({
                              ...c,
                              [f.key]: Number(e.target.value),
                            }))
                          }
                          disabled={!canAdmin || busy}
                          required
                        />
                      </label>
                    ))}
                  </div>
                  <div className="scenario-row">
                    <span>Kịch bản</span>
                    {patterns.map((p) => (
                      <label className="scenario-check" key={p}>
                        <input
                          type="checkbox"
                          checked={config.patterns.includes(p)}
                          disabled={!canAdmin || busy}
                          onChange={(e) =>
                            setConfig((c) => ({
                              ...c,
                              patterns: e.target.checked
                                ? [...c.patterns, p]
                                : c.patterns.filter((v) => v !== p),
                            }))
                          }
                        />
                        {names[p]}
                      </label>
                    ))}
                  </div>
                  <div className="config-footer">
                    <p>
                      <ShieldCheck size={16} />
                      Sổ cái kiểm tra balance, nonce và fee trước khi append.
                    </p>
                    <button className="primary" disabled={!canAdmin || busy}>
                      <Play size={16} />
                      {busy ? "Đang tạo…" : "Bắt đầu mô phỏng"}
                    </button>
                  </div>
                </form>
              </section>
              <div className="overview-grid">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Toàn cảnh dòng tiền</h2>
                      <p>Mẫu đồ thị có giới hạn từ lần chạy đang chọn.</p>
                    </div>
                    <button className="plain" onClick={() => setTab("graph")}>
                      Khám phá
                      <ArrowUpRight size={16} />
                    </button>
                  </div>
                  <div className="graph-preview">
                    <Graph data={graph} onNode={onNode} onEdge={onEdge} />
                    {!graph?.nodes.length && (
                      <div className="graph-empty">
                        <Network size={38} />
                        <p>
                          {run?.status === "running"
                            ? "Đang ghi dữ liệu…"
                            : "Tạo mô phỏng để khám phá dòng tiền."}
                        </p>
                      </div>
                    )}
                  </div>
                  <div className="graph-legend">
                    <span>
                      <i style={{ background: "#93c5b7" }} />
                      Low
                    </span>
                    <span>
                      <i style={{ background: "#e98255" }} />
                      High
                    </span>
                    <span>
                      <i style={{ background: "#d55361" }} />
                      Critical
                    </span>
                    <small>
                      {graph?.nodes.length ?? 0} nodes ·{" "}
                      {graph?.edges.length ?? 0} edges{" "}
                      {graph?.truncated && "· partial"}
                    </small>
                  </div>
                </section>
                <section className="panel signals">
                  <div className="panel-heading">
                    <div>
                      <h2>Các tín hiệu đã phát hiện</h2>
                      <p>Rule-based + temporal graph</p>
                    </div>
                  </div>
                  {Object.entries(metrics?.alerts_by_detector ?? {}).map(
                    ([code, count]) => (
                      <button
                        className="signal-row"
                        key={code}
                        onClick={() => setTab("alerts")}
                      >
                        <div>
                          <b>{names[code] ?? code}</b>
                          <small>{code}</small>
                        </div>
                        <strong>{count}</strong>
                        <ChevronRight size={14} />
                      </button>
                    ),
                  )}
                  {!Object.keys(metrics?.alerts_by_detector ?? {}).length && (
                    <div className="empty">
                      <ShieldCheck size={30} />
                      <p>Chưa có kết quả phân tích.</p>
                    </div>
                  )}
                  <div className="signal-foot">
                    Không có cảnh báo không đồng nghĩa đã được xác định an toàn.
                  </div>
                </section>
              </div>
              <TransactionTable
                txs={txs}
                onPick={onEdge}
                cursor={cursor}
                onMore={() =>
                  void act(async () => {
                    if (!selected || !cursor) return;
                    const result = await api<{
                      items: Tx[];
                      next_cursor: string | null;
                    }>(
                      "/transactions?" +
                        query({ run_id: selected, limit: 20, cursor }),
                    );
                    setTxs((t) => [...t, ...result.items]);
                    setCursor(result.next_cursor);
                  })
                }
              />
            </>
          )}

          {tab === "graph" && (
            <>
              <section className="panel explorer">
                <div className="explorer-toolbar">
                  <div className="input-icon">
                    <Search size={16} />
                    <input
                      aria-label="Địa chỉ trung tâm"
                      placeholder="Địa chỉ ví trung tâm…"
                      value={center}
                      onChange={(e) => setCenter(e.target.value)}
                    />
                  </div>
                  <label>
                    Hop
                    <select
                      value={hops}
                      onChange={(e) => setHops(Number(e.target.value))}
                    >
                      {[1, 2, 3, 4, 5].map((v) => (
                        <option key={v}>{v}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="plain"
                    onClick={() => {
                      setDetail(null);
                      setGraphParams({
                        center: center || undefined,
                        hops,
                        as_of_index: cutoff ?? undefined,
                      });
                    }}
                  >
                    <Filter size={15} />
                    Áp dụng
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Tải lại đồ thị"
                    onClick={() => setGraphTick((v) => v + 1)}
                  >
                    <RefreshCw size={17} />
                  </button>
                  <span className="subtle-tag">
                    {graph?.nodes.length ?? 0} NODES /{" "}
                    {graph?.edges.length ?? 0} EDGES
                  </span>
                </div>
                <div className="explorer-body">
                  <div className="graph-stage">
                    <Graph data={graph} onNode={onNode} onEdge={onEdge} />
                    {graphLoading && (
                      <div className="graph-loading">Đang tải đồ thị…</div>
                    )}
                    {!graphLoading && !graph?.nodes.length && (
                      <div className="graph-empty">
                        <Network size={40} />
                        <p>Không có giao dịch tại mốc đã chọn.</p>
                      </div>
                    )}
                  </div>
                  <aside className="inspector">
                    <span className="eyebrow">Bằng chứng & thông tin</span>
                    {wallet ? (
                      <>
                        <h3>Ví được chọn</h3>
                        <code className="break">{wallet.address}</code>
                        <dl>
                          <dt>Số dư tại cutoff</dt>
                          <dd>{formatMoney(wallet.balance_base)} SIM</dd>
                          <dt>Dòng vào</dt>
                          <dd>{formatMoney(wallet.inflow_base)} SIM</dd>
                          <dt>Dòng ra + phí</dt>
                          <dd>
                            {formatMoney(wallet.outflow_with_fees_base)} SIM
                          </dd>
                        </dl>
                      </>
                    ) : edge ? (
                      <>
                        <h3>Giao dịch được chọn</h3>
                        <code className="break">{edge.tx_hash}</code>
                        <dl>
                          <dt>Từ</dt>
                          <dd>{short(edge.from_address)}</dd>
                          <dt>Đến</dt>
                          <dd>{short(edge.to_address)}</dd>
                          <dt>Lượng chuyển</dt>
                          <dd>{formatMoney(edge.value_base)} SIM</dd>
                          <dt>Order</dt>
                          <dd>#{edge.global_index}</dd>
                          <dt>Thời gian UTC</dt>
                          <dd>{new Date(edge.timestamp).toISOString()}</dd>
                        </dl>
                      </>
                    ) : detail ? (
                      <AlertInspector alert={detail} />
                    ) : (
                      <div className="empty">
                        <ArrowDownLeft size={30} />
                        <h3>Chọn một ví hoặc giao dịch</h3>
                        <p>Hoặc mở cảnh báo để làm nổi bật đường bằng chứng.</p>
                      </div>
                    )}
                  </aside>
                </div>
                <div className="timeline">
                  <Layers3 size={18} />
                  <label htmlFor="timeline">As-of order</label>
                  <input
                    id="timeline"
                    type="range"
                    min="0"
                    max={Math.max(0, (run?.transaction_count ?? 1) - 1)}
                    value={
                      cutoff ?? Math.max(0, (run?.transaction_count ?? 1) - 1)
                    }
                    onChange={(e) => setCutoff(Number(e.target.value))}
                    onPointerUp={() => {
                      setDetail(null);
                      setGraphParams({
                        center: center || undefined,
                        hops,
                        as_of_index: cutoff ?? undefined,
                      });
                    }}
                    onKeyUp={() => {
                      setDetail(null);
                      setGraphParams({
                        center: center || undefined,
                        hops,
                        as_of_index: cutoff ?? undefined,
                      });
                    }}
                    disabled={!run?.transaction_count}
                  />
                  <span>#{graph?.as_of_index ?? "—"}</span>
                </div>
                <div className="graph-legend">
                  <span>
                    <i style={{ background: "#93c5b7" }} />
                    Low
                  </span>
                  <span>
                    <i style={{ background: "#e98255" }} />
                    High
                  </span>
                  <span>
                    <i style={{ background: "#d55361" }} />
                    Critical
                  </span>
                  <small>
                    {graph?.truncated
                      ? "Đồ thị bị cắt theo budget · coverage partial"
                      : graph?.analysis_mode === "retrospective"
                        ? "Ledger đến cutoff - TI hồi cứu"
                        : graph?.coverage === "partial"
                          ? "Phân tích một phần đến cutoff"
                          : "Chỉ hiển thị dữ liệu có sẵn đến cutoff"}{" "}
                    · Hypothesis: haircut
                  </small>
                </div>
              </section>
              {detail && (
                <section className="panel evidence-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>Bằng chứng: {names[detail.detector_code]}</h2>
                      <p>
                        {detail.evidence.length} giao dịch ·{" "}
                        {detail.analysis_version}
                      </p>
                    </div>
                    <Badge value={detail.severity} />
                  </div>
                  <div className="evidence-list">
                    {detail.transactions?.map((t) => (
                      <button key={t.tx_hash} onClick={() => onEdge(t.tx_hash)}>
                        <code>
                          #{t.global_index} · {short(t.tx_hash)}
                        </code>
                        <span>
                          {short(t.from_address)} <ArrowRight size={13} />{" "}
                          {short(t.to_address)}
                        </span>
                        <b>{formatMoney(t.value_base)} SIM</b>
                      </button>
                    ))}
                  </div>
                  {user.role !== "Viewer" && (
                    <form
                      className="review-form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void act(async () => {
                          await api(
                            `/alerts/${detail.id}/review?${query({ run_id: selected! })}`,
                            "PATCH",
                            {
                              status: reviewStatus,
                              comment: reviewComment,
                              revision: detail.revision,
                            },
                          );
                          const refreshed = await api<Alert>(
                            `/alerts/${detail.id}?${query({ run_id: selected! })}`,
                          );
                          setDetail(refreshed);
                          setReviewComment("");
                          refreshRef.current();
                        });
                      }}
                    >
                      <label>
                        Trạng thái
                        <select
                          value={reviewStatus}
                          onChange={(e) => setReviewStatus(e.target.value)}
                        >
                          <option value="open">Mới</option>
                          <option value="in_review">Đang xem xét</option>
                          <option value="closed">Đã đóng</option>
                        </select>
                      </label>
                      <label>
                        Nhận xét analyst
                        <input
                          required
                          maxLength={2000}
                          value={reviewComment}
                          onChange={(e) => setReviewComment(e.target.value)}
                          placeholder="Ghi lý do và kết quả đánh giá…"
                        />
                      </label>
                      <button className="primary" disabled={busy}>
                        Lưu đánh giá
                      </button>
                    </form>
                  )}
                  {detail.reviews?.map((r, i) => (
                    <div className="review-history" key={i}>
                      <b>{r.username}</b> · <Badge value={r.status} /> ·{" "}
                      {r.comment}
                    </div>
                  ))}
                </section>
              )}
            </>
          )}

          {tab === "alerts" && (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Hàng đợi xem xét</h2>
                    <p>
                      Cảnh báo là tín hiệu để điều tra, không phải kết luận pháp
                      lý.
                    </p>
                  </div>
                  <select
                    aria-label="Lọc trạng thái cảnh báo"
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                  >
                    <option value="">Tất cả trạng thái</option>
                    <option value="open">Mới</option>
                    <option value="in_review">Đang xem xét</option>
                    <option value="closed">Đã đóng</option>
                  </select>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Mẫu nghi vấn</th>
                        <th>Ưu tiên</th>
                        <th>Bằng chứng</th>
                        <th>Trạng thái</th>
                        <th>Coverage</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {alerts.map((a) => (
                        <tr key={a.id}>
                          <td>
                            <b>{names[a.detector_code] ?? a.detector_code}</b>
                            <small>{short(a.anchor)}</small>
                          </td>
                          <td>
                            <b>{a.score}/100</b> <Badge value={a.severity} />
                          </td>
                          <td>{a.evidence.length} giao dịch</td>
                          <td>
                            <Badge value={a.status} />
                          </td>
                          <td>
                            <span
                              className={
                                a.coverage === "partial" ? "warning-text" : ""
                              }
                            >
                              {a.coverage}
                            </span>
                          </td>
                          <td>
                            <button
                              className="plain"
                              onClick={() => void openAlert(a)}
                            >
                              Xem bằng chứng
                              <ArrowUpRight size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!alerts.length && (
                    <div className="empty">
                      <ShieldCheck size={32} />
                      <p>Chưa có cảnh báo cho bộ lọc này.</p>
                    </div>
                  )}
                </div>
              </section>
              <section className="panel evaluation">
                <div className="panel-heading">
                  <div>
                    <h2>Đánh giá độc lập</h2>
                    <p>{metrics?.scope ?? "Đợi evaluator hoàn tất."}</p>
                  </div>
                  <button
                    className="plain"
                    disabled={!metrics || metrics.status === "pending"}
                    onClick={() => {
                      const blob = new Blob(
                        [JSON.stringify(metrics, null, 2)],
                        { type: "application/json" },
                      );
                      const link = document.createElement("a");
                      link.href = URL.createObjectURL(blob);
                      link.download = `metrics-${selected}.json`;
                      link.click();
                      URL.revokeObjectURL(link.href);
                    }}
                  >
                    <Download size={16} />
                    Xuất metrics
                  </button>
                </div>
                <div className="evaluation-grid">
                  {[
                    {
                      label:
                        metrics?.analysis_mode === "retrospective"
                          ? "Sequential · TI hồi cứu"
                          : "Transaction online (as-known)",
                      data:
                        metrics?.online ??
                        metrics?.sequential_with_retrospective_feed,
                    },
                    {
                      label: "Transaction retrospective",
                      data: metrics?.retrospective,
                    },
                  ].map((m) => (
                    <div key={m.label}>
                      <h3>{m.label}</h3>
                      <dl>
                        <dt>Precision</dt>
                        <dd>{percent(m.data?.precision)}</dd>
                        <dt>Recall</dt>
                        <dd>{percent(m.data?.recall)}</dd>
                        <dt>False positive rate</dt>
                        <dd>{percent(m.data?.FPR)}</dd>
                        <dt>TP / FP / FN / TN</dt>
                        <dd>
                          {m.data
                            ? `${m.data.TP} / ${m.data.FP} / ${m.data.FN} / ${m.data.TN}`
                            : "—"}
                        </dd>
                      </dl>
                    </div>
                  ))}
                  <div>
                    <h3>Hiệu năng lần chạy</h3>
                    <dl>
                      <dt>Worker throughput</dt>
                      <dd>{numeric(metrics?.throughput_tx_s, 1)} tx/s</dd>
                      <dt>Engine p95 / tx</dt>
                      <dd>{numeric(metrics?.process_p95_ms, 2)} ms</dd>
                      <dt>Partial assessments</dt>
                      <dd>{numeric(metrics?.partial_assessments)}</dd>
                      <dt>ML</dt>
                      <dd>Chưa cấu hình · A=0</dd>
                    </dl>
                  </div>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Pattern</th>
                        <th>Campaign</th>
                        <th>Core overlap</th>
                        <th>
                          {metrics?.analysis_mode === "retrospective"
                            ? "Sequential recall · TI hồi cứu"
                            : "Online recall"}
                        </th>
                        <th>Retrospective recall</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(metrics?.patterns ?? {}).map(([p, m]) => (
                        <tr key={p}>
                          <td>{names[p] ?? p}</td>
                          <td>
                            {m.campaign_detected ? "Đã phát hiện" : "Chưa đủ"}
                          </td>
                          <td>{percent(m.core_overlap)}</td>
                          <td>
                            {percent(
                              m.online?.recall ??
                                m.sequential_with_retrospective_feed?.recall,
                            )}
                          </td>
                          <td>{percent(m.retrospective.recall)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="evaluation-note">
                  Số liệu của một lần mô phỏng; chưa đại diện chất lượng trên
                  unseen runs. Mục tiêu benchmark trong kế hoạch được báo riêng
                  với số đo.
                </div>
              </section>
            </>
          )}
          {run?.error && (
            <div className="error">
              {run.error}
              {canAdmin && (
                <button
                  className="plain"
                  onClick={() =>
                    void act(async () => {
                      await api("/simulations/" + selected + "/retry", "POST");
                      refreshRef.current();
                    })
                  }
                >
                  Thử lại job
                </button>
              )}
            </div>
          )}
          {run?.status === "completed" && canAdmin && (
            <div className="replay-strip">
              <div>
                <RefreshCw size={18} />
                <span>
                  Phân tích lại ledger bất biến với snapshot feed mới.
                </span>
              </div>
              <button
                className="plain"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await api(
                      "/simulations/" + selected + "/replay",
                      "POST",
                      { mode: "as-known" },
                      undefined,
                      { "Idempotency-Key": crypto.randomUUID() },
                    );
                    refreshRef.current();
                  })
                }
              >
                Replay as-known
              </button>
              <button
                className="plain"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await api(
                      "/simulations/" + selected + "/replay",
                      "POST",
                      { mode: "retrospective" },
                      undefined,
                      { "Idempotency-Key": crypto.randomUUID() },
                    );
                    refreshRef.current();
                  })
                }
              >
                Replay hồi cứu
              </button>
            </div>
          )}
          <footer>
            <span>
              <ShieldCheck size={14} />
              Tài sản giả lập SIM · Hash sim-sha256-v1 · Finality simulated
            </span>
            <span>Ưu tiên xem xét ≠ xác suất phạm tội</span>
          </footer>
        </div>
      </main>
      {feedOpen && (
        <div className="modal-backdrop">
          <section
            className="modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feed-title"
          >
            <div className="panel-heading">
              <div>
                <h2 id="feed-title">Thêm phiên bản threat intelligence</h2>
                <p>Địa chỉ tổng hợp. Không thực thi expression hoặc tải URL.</p>
              </div>
              <button
                className="icon-button"
                aria-label="Đóng nguồn TI"
                onClick={() => setFeedOpen(false)}
              >
                <X />
              </button>
            </div>
            <textarea
              aria-label="Feed JSON"
              rows={16}
              value={feedJSON}
              onChange={(e) => setFeedJSON(e.target.value)}
            />
            <p>
              Feed mới được dùng qua replay. known_at ở tương lai so với giao
              dịch chỉ khả dụng trong phân tích hồi cứu.
            </p>
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await api("/feeds", "POST", JSON.parse(feedJSON));
                  setFeedOpen(false);
                })
              }
            >
              Lưu phiên bản feed
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
function AlertInspector({ alert }: { alert: Alert }) {
  return (
    <>
      <h3>{names[alert.detector_code] ?? alert.detector_code}</h3>
      <div className="risk-score">
        <strong>
          {alert.score}
          <small>/100</small>
        </strong>
        <Badge value={alert.severity} />
      </div>
      <p className="muted">Mức ưu tiên xem xét</p>
      <div className="components">
        {Object.entries(alert.assessment.components).map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            <div>
              <i style={{ width: v * 100 + "%" }} />
            </div>
            <b>{v.toFixed(2)}</b>
          </div>
        ))}
      </div>
      <dl>
        <dt>Blend</dt>
        <dd>{alert.assessment.blend.toFixed(2)}</dd>
        <dt>Floor áp dụng</dt>
        <dd>{Object.values(alert.assessment.floors).join(" / ") || "0"}</dd>
        <dt>Coverage</dt>
        <dd>{alert.coverage}</dd>
        <dt>Model</dt>
        <dd>A=0 · baseline</dd>
      </dl>
      <h4>Quan sát & ngưỡng</h4>
      <pre>
        {JSON.stringify(
          { observed: alert.observed, thresholds: alert.thresholds },
          null,
          2,
        )}
      </pre>
      {alert.assessment.partial_reasons.length > 0 && (
        <p className="warning-text">
          {alert.assessment.partial_reasons.join(", ")}
        </p>
      )}
    </>
  );
}
function TransactionTable({
  txs,
  cursor,
  onMore,
  onPick,
}: {
  txs: Tx[];
  cursor: string | null;
  onMore: () => void;
  onPick: (hash: string) => void;
}) {
  return (
    <section className="panel transactions">
      <div className="panel-heading">
        <div>
          <h2>Giao dịch trong sổ cái</h2>
          <p>Thứ tự xác định theo block_height / tx_index. Thời gian UTC.</p>
        </div>
        <span className="subtle-tag">{txs.length} BẢN GHI</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Order / Transaction</th>
              <th>Từ → Đến</th>
              <th>Lượng chuyển</th>
              <th>Fee</th>
              <th>Ưu tiên</th>
              <th>UTC</th>
            </tr>
          </thead>
          <tbody>
            {txs.map((t) => (
              <tr key={t.tx_hash}>
                <td>
                  <button
                    className="hash-link"
                    onClick={() => onPick(t.tx_hash)}
                  >
                    #{t.global_index} · {short(t.tx_hash)}
                  </button>
                  <small>
                    Block {t.block_height} / Index {t.tx_index}
                  </small>
                </td>
                <td>
                  <code>
                    {short(t.from_address)} → {short(t.to_address)}
                  </code>
                </td>
                <td>
                  <b>{formatMoney(t.value_base)} SIM</b>
                </td>
                <td>{formatMoney(t.fee_base)}</td>
                <td>
                  {t.assessment ? (
                    <>
                      <b>{t.assessment.score}</b>{" "}
                      <Badge value={t.assessment.severity} />
                    </>
                  ) : (
                    "Đang phân tích"
                  )}
                </td>
                <td>
                  {new Date(t.timestamp)
                    .toISOString()
                    .slice(0, 19)
                    .replace("T", " ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!txs.length && (
          <div className="empty">
            <Layers3 size={30} />
            <p>Chưa có giao dịch.</p>
          </div>
        )}
      </div>
      {cursor && (
        <div className="table-more">
          <button className="plain" onClick={onMore}>
            Tải thêm giao dịch
            <ChevronRight size={15} />
          </button>
        </div>
      )}
    </section>
  );
}
