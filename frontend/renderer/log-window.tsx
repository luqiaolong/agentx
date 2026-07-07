import React from "react";
import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";
import {
  Trash2,
  ScrollText,
  Minus,
  X,
  Search,
  Filter,
  Bug,
  Info,
  AlertTriangle,
  AlertCircle,
} from "lucide-react";
import "./styles/globals.css";

interface LogLine {
  id: number;
  text: string;
  timestamp: string;
  level: LogLevel;
}

type LogLevel = "error" | "warn" | "info" | "debug" | "none";

let globalId = 0;
const MAX_LINES = 5000;

/* ================================================================
 *  Design System — 与主窗口 (App.tsx) 保持一致
 *  颜色:   CSS 变量来自 globals.css (.dark)
 *  字体:   Inter (UI) + JetBrains Mono (日志内容)
 *  标题栏: glass-card 毛玻璃效果
 * ================================================================ */

const DS = {
  // Backgrounds
  bgApp: "#111111",
  bgSurface: "#1a1a1a",
  bgSubtle: "#242424",
  bgHover: "#2e2e2e",
  // Borders
  borderDefault: "#333333",
  borderStrong: "#444444",
  // Text
  textPrimary: "#ececec",
  textSecondary: "#c8c8c8",
  textMuted: "#909090",
  // Accents (level colors — keep distinct for readability)
  levelError: "#f87171",
  levelWarn: "#fbbf24",
  levelInfo: "#60a5fa",
  levelDebug: "#a78bfa",
  // Misc
  brandIndigo: "#4f46e5",
  success: "#22c55e",
  fontSans: '"Inter", "Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  fontMono: '"JetBrains Mono", "SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace',
} as const;

function detectLevel(text: string): LogLevel {
  const t = text.toUpperCase();
  if (t.includes("ERROR") || t.includes("ERR") || t.includes("EXCEPTION") || t.includes("FATAL")) {
    return "error";
  }
  if (t.includes("WARN") || t.includes("WARNING")) {
    return "warn";
  }
  if (t.includes("DEBUG")) {
    return "debug";
  }
  if (t.includes("INFO")) {
    return "info";
  }
  return "none";
}

function getLevelColor(level: LogLevel): string {
  switch (level) {
    case "error":
      return DS.levelError;
    case "warn":
      return DS.levelWarn;
    case "info":
      return DS.levelInfo;
    case "debug":
      return DS.levelDebug;
    default:
      return DS.textSecondary;
  }
}

function getLevelBg(level: LogLevel): string {
  switch (level) {
    case "error":
      return "rgba(248, 113, 113, 0.10)";
    case "warn":
      return "rgba(251, 191, 36, 0.06)";
    case "info":
      return "rgba(96, 165, 250, 0.06)";
    case "debug":
      return "rgba(167, 139, 250, 0.05)";
    default:
      return "transparent";
  }
}

function getLevelIcon(level: LogLevel) {
  switch (level) {
    case "error":
      return <AlertCircle size={10} />;
    case "warn":
      return <AlertTriangle size={10} />;
    case "info":
      return <Info size={10} />;
    case "debug":
      return <Bug size={10} />;
    default:
      return null;
  }
}

function getLevelLabel(level: LogLevel): string {
  switch (level) {
    case "error":
      return "ERR";
    case "warn":
      return "WARN";
    case "info":
      return "INFO";
    case "debug":
      return "DBG";
    default:
      return "";
  }
}

function LogWindow() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterLevel, setFilterLevel] = useState<LogLevel | "all">("all");
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const filterMenuRef = useRef<HTMLDivElement>(null);

  // 监听日志事件（使用 getCurrentWebviewWindow().listen 确保事件能正确接收）
  useEffect(() => {
    let unlistenFn: UnlistenFn | null = null;

    const setup = async () => {
      try {
        const win = getCurrentWebviewWindow();
        console.log("[LogWindow] registering listener on window:", win.label);
        unlistenFn = await win.listen<string>("log:append", (event) => {
          console.log("[LogWindow] received log:append event:", event.payload);
          const text = event.payload;
          const now = new Date();
          const timestamp = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}:${now.getSeconds().toString().padStart(2, "0")}.${now.getMilliseconds().toString().padStart(3, "0")}`;
          const level = detectLevel(text);

          setLines((prev) => {
            const next = [...prev, { id: globalId++, text, timestamp, level }];
            if (next.length > MAX_LINES) {
              return next.slice(next.length - MAX_LINES);
            }
            return next;
          });
        });
        console.log("[LogWindow] listener registered successfully");
      } catch (e) {
        console.error("[LogWindow] failed to register listener:", e);
      }
    };

    setup();

    return () => {
      if (unlistenFn) {
        unlistenFn();
      }
    };
  }, []);

  // 自动滚动
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines, autoScroll]);

  // 点击外部关闭 filter menu
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node)) {
        setShowFilterMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 80;
    setAutoScroll(isNearBottom);
  }, []);

  const clearLogs = useCallback(() => setLines([]), []);

  const handleMinimize = useCallback(async () => {
    const win = getCurrentWebviewWindow();
    await win.minimize();
  }, []);

  const handleClose = useCallback(async () => {
    const win = getCurrentWebviewWindow();
    await win.close();
  }, []);

  const toggleSearch = useCallback(() => {
    setIsSearching((v) => {
      const next = !v;
      if (next) {
        setTimeout(() => searchInputRef.current?.focus(), 50);
      } else {
        setSearchQuery("");
      }
      return next;
    });
  }, []);

  // 过滤后的日志
  const filteredLines = useMemo(() => {
    let result = lines;
    if (filterLevel !== "all") {
      result = result.filter((l) => l.level === filterLevel);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter((l) => l.text.toLowerCase().includes(q));
    }
    return result;
  }, [lines, filterLevel, searchQuery]);

  // 统计各级别数量
  const levelCounts = useMemo(() => {
    const counts: Record<LogLevel, number> = { error: 0, warn: 0, info: 0, debug: 0, none: 0 };
    for (const line of lines) {
      counts[line.level]++;
    }
    return counts;
  }, [lines]);

  const highlightText = (text: string, query: string) => {
    if (!query.trim()) return text;
    const q = query.toLowerCase();
    if (!text.toLowerCase().includes(q)) return text;

    const parts: (string | JSX.Element)[] = [];
    let lastIndex = 0;
    const lowerText = text.toLowerCase();
    let idx = lowerText.indexOf(q);

    while (idx !== -1) {
      if (idx > lastIndex) {
        parts.push(text.slice(lastIndex, idx));
      }
      parts.push(
        <mark
          key={`${idx}-${lastIndex}`}
          style={{
            background: "rgba(251, 191, 36, 0.30)",
            color: DS.levelWarn,
            borderRadius: 2,
            padding: "0 1px",
          }}
        >
          {text.slice(idx, idx + q.length)}
        </mark>
      );
      lastIndex = idx + q.length;
      idx = lowerText.indexOf(q, lastIndex);
    }
    if (lastIndex < text.length) {
      parts.push(text.slice(lastIndex));
    }
    return parts;
  };

  const filterOptions: { level: LogLevel | "all"; label: string; color: string; count?: number }[] = [
    { level: "all", label: "全部", color: DS.textMuted, count: lines.length },
    { level: "error", label: "错误", color: DS.levelError, count: levelCounts.error },
    { level: "warn", label: "警告", color: DS.levelWarn, count: levelCounts.warn },
    { level: "info", label: "信息", color: DS.levelInfo, count: levelCounts.info },
    { level: "debug", label: "调试", color: DS.levelDebug, count: levelCounts.debug },
  ];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        width: "100vw",
        background: DS.bgApp,
        color: DS.textPrimary,
        fontFamily: DS.fontSans,
        fontSize: 12,
        lineHeight: "1.6",
        overflow: "hidden",
        margin: 0,
        padding: 0,
        border: "none",
        outline: "none",
        boxSizing: "border-box",
      }}
    >
      {/* ===== 标题栏 —— glass-card 风格，与主窗口一致 ===== */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 0 0 12px",
          height: 40,
          borderBottom: `1px solid ${DS.borderDefault}`,
          backgroundColor: "color-mix(in srgb, #1a1a1a 85%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          flexShrink: 0,
          userSelect: "none",
          // @ts-ignore
          WebkitAppRegion: "drag",
        }}
      >
        {/* 左侧：Logo + 标题 + 统计 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontWeight: 600,
            fontSize: 13,
            fontFamily: DS.fontSans,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              borderRadius: 5,
              background: DS.brandIndigo,
              color: DS.textSecondary,
              boxShadow: "0 1px 3px 0 rgb(0 0 0 / 0.1)",
            }}
          >
            <ScrollText size={13} />
          </div>
          <span style={{ color: DS.textPrimary, letterSpacing: 0.3 }}>AgentX</span>
          <span style={{ color: DS.textMuted, fontWeight: 400 }}>Logs</span>

          {/* 统计徽章 */}
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 4 }}>
            {levelCounts.error > 0 && <LevelBadge color={DS.levelError} count={levelCounts.error} />}
            {levelCounts.warn > 0 && <LevelBadge color={DS.levelWarn} count={levelCounts.warn} />}
            <LevelBadge color={DS.textMuted} count={lines.length} label="总" />
          </div>
        </div>

        {/* 右侧：工具按钮 + 窗口控制 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            // @ts-ignore
            WebkitAppRegion: "no-drag",
            height: "100%",
          }}
        >
          {/* 搜索按钮 */}
          <IconButton
            active={isSearching}
            onClick={toggleSearch}
            title="搜索 (Ctrl+F)"
            icon={<Search size={13} />}
          />

          {/* 过滤按钮 */}
          <div style={{ position: "relative" }} ref={filterMenuRef}>
            <IconButton
              active={filterLevel !== "all"}
              onClick={() => setShowFilterMenu((v) => !v)}
              title="过滤级别"
              icon={<Filter size={13} />}
            />
            {showFilterMenu && (
              <div
                style={{
                  position: "absolute",
                  top: 36,
                  right: 0,
                  background: DS.bgSurface,
                  border: `1px solid ${DS.borderDefault}`,
                  borderRadius: 8,
                  padding: "4px",
                  minWidth: 120,
                  zIndex: 100,
                  boxShadow: "0 12px 32px -4px rgb(0 0 0 / 0.16), 0 4px 12px -2px rgb(0 0 0 / 0.08)",
                }}
              >
                {filterOptions.map((opt) => (
                  <button
                    key={opt.level}
                    onClick={() => {
                      setFilterLevel(opt.level);
                      setShowFilterMenu(false);
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      width: "100%",
                      padding: "6px 10px",
                      borderRadius: 5,
                      border: "none",
                      background: filterLevel === opt.level ? "rgba(79, 70, 229, 0.15)" : "transparent",
                      color: filterLevel === opt.level ? "#a5b4fc" : DS.textSecondary,
                      cursor: "pointer",
                      fontSize: 12,
                      fontFamily: DS.fontSans,
                      transition: "background 0.15s",
                    }}
                    onMouseEnter={(e) => {
                      if (filterLevel !== opt.level) e.currentTarget.style.background = DS.bgHover;
                    }}
                    onMouseLeave={(e) => {
                      if (filterLevel !== opt.level) e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: opt.color,
                          display: "inline-block",
                        }}
                      />
                      {opt.label}
                    </span>
                    <span style={{ color: DS.textMuted, fontSize: 11 }}>{opt.count}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ width: 1, height: 18, background: DS.borderDefault, margin: "0 4px" }} />

          {/* 自动滚动开关 */}
          <button
            onClick={() => setAutoScroll((v) => !v)}
            title={autoScroll ? "自动滚动开启" : "自动滚动关闭"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: 5,
              border: "none",
              background: autoScroll ? "rgba(34, 197, 94, 0.12)" : "transparent",
              color: autoScroll ? DS.success : DS.textMuted,
              cursor: "pointer",
              fontSize: 11,
              fontFamily: DS.fontSans,
              fontWeight: 500,
              transition: "all 0.15s",
            }}
            onMouseEnter={(e) => {
              if (!autoScroll) e.currentTarget.style.background = DS.bgHover;
            }}
            onMouseLeave={(e) => {
              if (!autoScroll) e.currentTarget.style.background = "transparent";
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: autoScroll ? DS.success : DS.textMuted,
                transition: "background 0.2s",
              }}
            />
            自动滚动
          </button>

          {/* 清空按钮 */}
          <IconButton
            onClick={clearLogs}
            title="清空日志"
            icon={<Trash2 size={13} />}
            hoverColor={DS.levelError}
          />

          <div style={{ width: 1, height: 18, background: DS.borderDefault, margin: "0 4px" }} />

          {/* 窗口控制 */}
          <WindowControlButton onClick={handleMinimize} title="最小化">
            <Minus size={13} />
          </WindowControlButton>
          <WindowControlButton onClick={handleClose} title="关闭" hoverBg="#ef4444" hoverColor="#fff">
            <X size={13} />
          </WindowControlButton>
        </div>
      </div>

      {/* ===== 搜索栏（条件展开） ===== */}
      {isSearching && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 12px",
            borderBottom: `1px solid ${DS.borderDefault}`,
            background: DS.bgApp,
            flexShrink: 0,
          }}
        >
          <Search size={14} color={DS.textMuted} />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索日志内容..."
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: DS.textPrimary,
              fontFamily: DS.fontSans,
              fontSize: 12,
              padding: "2px 0",
            }}
          />
          {searchQuery && (
            <span style={{ color: DS.textMuted, fontSize: 11, whiteSpace: "nowrap" }}>
              {filteredLines.length} 条匹配
            </span>
          )}
          <button
            onClick={toggleSearch}
            style={{
              background: "transparent",
              border: "none",
              color: DS.textMuted,
              cursor: "pointer",
              padding: 2,
              display: "flex",
              alignItems: "center",
            }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ===== 日志内容区 ===== */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflow: "auto",
          padding: "4px 0",
          wordBreak: "break-all",
          whiteSpace: "pre-wrap",
          fontFamily: DS.fontMono,
        }}
      >
        {filteredLines.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: DS.textMuted,
              gap: 8,
              fontFamily: DS.fontSans,
            }}
          >
            <ScrollText size={32} opacity={0.3} />
            <span style={{ fontSize: 13 }}>
              {lines.length === 0 ? "暂无日志，等待后端输出..." : "没有匹配的日志"}
            </span>
          </div>
        ) : (
          filteredLines.map((line) => {
            const levelColor = getLevelColor(line.level);
            const levelBg = getLevelBg(line.level);
            const icon = getLevelIcon(line.level);
            const label = getLevelLabel(line.level);

            return (
              <div
                key={line.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 0,
                  padding: "2px 12px",
                  borderBottom: `1px solid rgba(51, 51, 51, 0.4)`,
                  background: levelBg,
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = DS.bgHover;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = levelBg;
                }}
              >
                {/* 时间戳 */}
                <span
                  style={{
                    color: DS.textMuted,
                    marginRight: 10,
                    userSelect: "none",
                    fontSize: 11,
                    minWidth: 70,
                    flexShrink: 0,
                    paddingTop: 1,
                    fontFamily: DS.fontMono,
                  }}
                >
                  {line.timestamp}
                </span>

                {/* 级别标签（紧凑） */}
                {label && (
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 3,
                      color: levelColor,
                      fontSize: 10,
                      fontWeight: 600,
                      minWidth: 34,
                      flexShrink: 0,
                      marginRight: 8,
                      paddingTop: 2,
                      opacity: 0.85,
                      fontFamily: DS.fontMono,
                    }}
                  >
                    {icon}
                    {label}
                  </span>
                )}

                {/* 日志内容 */}
                <span style={{ color: levelColor, flex: 1, paddingTop: 1, fontFamily: DS.fontMono }}>
                  {highlightText(line.text, searchQuery)}
                </span>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>

      {/* ===== 底部状态栏 ===== */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px 12px",
          borderTop: `1px solid ${DS.borderDefault}`,
          background: DS.bgApp,
          flexShrink: 0,
          fontSize: 11,
          color: DS.textSecondary,
          userSelect: "none",
          fontFamily: DS.fontSans,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ color: DS.textMuted }}>总计 {lines.length} 条</span>
          {filterLevel !== "all" && (
            <span style={{ color: DS.levelInfo }}>
              过滤: {filterOptions.find((o) => o.level === filterLevel)?.label} ({filteredLines.length})
            </span>
          )}
          {searchQuery && (
            <span style={{ color: DS.levelWarn }}>
              搜索: "{searchQuery}" ({filteredLines.length})
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              color: DS.success,
              fontWeight: 500,
              fontFamily: DS.fontSans,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: DS.success,
                boxShadow: "0 0 6px rgba(34, 197, 94, 0.5)",
              }}
            />
            监听中
          </span>
        </div>
      </div>
    </div>
  );
}

// ===== 子组件 =====

function LevelBadge({ color, count, label }: { color: string; count: number; label?: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        fontSize: 10,
        fontWeight: 600,
        padding: "1px 6px",
        borderRadius: 10,
        background: `${color}18`,
        color,
        border: `1px solid ${color}25`,
        fontFamily: DS.fontSans,
      }}
    >
      {label && <span style={{ opacity: 0.6, fontWeight: 400 }}>{label}</span>}
      {count}
    </span>
  );
}

function IconButton({
  onClick,
  title,
  icon,
  active = false,
  hoverColor,
}: {
  onClick: () => void;
  title: string;
  icon: React.ReactNode;
  active?: boolean;
  hoverColor?: string;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 28,
        height: 28,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 5,
        border: "none",
        background: active ? "rgba(79, 70, 229, 0.12)" : "transparent",
        color: hovered ? hoverColor || DS.textPrimary : active ? "#a5b4fc" : DS.textMuted,
        cursor: "pointer",
        fontSize: 13,
        padding: 0,
        transition: "all 0.15s",
      }}
    >
      {icon}
    </button>
  );
}

function WindowControlButton({
  onClick,
  title,
  children,
  hoverBg,
  hoverColor,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
  hoverBg?: string;
  hoverColor?: string;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 36,
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        border: "none",
        background: hovered && hoverBg ? hoverBg : "transparent",
        color: hovered && hoverColor ? hoverColor : DS.textMuted,
        cursor: "pointer",
        fontSize: 13,
        padding: 0,
        transition: "all 0.15s",
        borderRadius: 0,
      }}
    >
      {children}
    </button>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element not found");

createRoot(rootEl).render(
  <React.StrictMode>
    <LogWindow />
  </React.StrictMode>,
);
