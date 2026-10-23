'use client';

// WATA: EasyUI's graph kept (its curves, dashed at rest and lit when traced, its springs and status bar), with:
// a node's health as a ring, an icon and a word (never colour alone); its whole name under it (EasyUI cut names to
// seven letters inside the circle); arrows from what calls to what is called, and each link's name while traced;
// nodes the keyboard reaches and traces (Enter keeps a trace, as a click does); the canvas's size given by the caller;
// a light theme.

import React, { useState, useMemo, useId } from 'react';
import { motion } from 'framer-motion';
import { cn } from '../../lib/utils';
import { motionTransitions } from '../../lib/motion-tokens';

/** WATA: a node's health, as the admin's services report it. */
export type TraceStatus = 'ok' | 'warning' | 'down' | 'unknown' | 'disabled';

export interface TraceNode {
  id: string;
  label: string;
  type?: 'service' | 'database' | 'api' | 'client';
  x?: number;
  y?: number;
  /** WATA: its health; none for what reports none (outside services). */
  status?: TraceStatus;
  /** WATA: the health in words ("Up", "Down"...), and what was observed. */
  statusLabel?: string;
  detail?: string;
}

export interface TraceConnection {
  from: string;
  to: string;
  label?: string;
  type?: string;
}

export interface DependencyTraceProps {
  /** Nodes in the dependency graph. */
  nodes: TraceNode[];
  /** Connection links between nodes. */
  connections: TraceConnection[];
  /** Selected node handler. */
  onNodeSelect?: (id: string) => void;
  /** Hovered node handler. */
  onNodeHover?: (id: string | null) => void;
  /** Radius/size of the node badge in pixels. Default is 36. */
  nodeSize?: number;
  /** WATA: the canvas the nodes' x and y are on (EasyUI's was 400 × 280). */
  width?: number;
  height?: number;
  /** WATA: the graph's name, for screen readers. */
  label?: string;
  /** Custom CSS class names. */
  className?: string;
}

// WATA: the admin's status colours (theme.css), each with its icon.
const STATUS_STROKE: Record<TraceStatus, string> = {
  ok: 'var(--positive)',
  warning: 'var(--warning)',
  down: 'var(--negative)',
  unknown: 'var(--chart-muted)',
  disabled: 'var(--chart-muted)',
};

const STATUS_ICON: Record<TraceStatus, string> = {
  ok: '✓',
  warning: '!',
  down: '✕',
  unknown: '?',
  disabled: '–',
};

export const DependencyTrace: React.FC<DependencyTraceProps> = ({
  nodes,
  connections,
  onNodeSelect,
  onNodeHover,
  nodeSize = 36,
  width = 400,
  height = 280,
  label = 'Dependency Relationship Graph',
  className,
}) => {
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const markerId = `${useId().replace(/:/g, '')}-arrow`;

  // Position nodes nicely on SVG canvas (hierarchical / circular layout)
  const positionedNodes = useMemo(() => {
    return nodes.map((node, i) => {
      if (node.x !== undefined && node.y !== undefined) return node;
      // Default radial positioning
      const total = nodes.length;
      if (total === 1) return { ...node, x: width / 2, y: height / 2 };
      const angle = (i / total) * 2 * Math.PI - Math.PI / 2;
      const radiusX = width * 0.35;
      const radiusY = height * 0.3;
      return {
        ...node,
        x: width / 2 + radiusX * Math.cos(angle),
        y: height / 2 + radiusY * Math.sin(angle),
      };
    });
  }, [nodes, width, height]);

  const activeId = hoveredNodeId || selectedNodeId;
  const activeNode = positionedNodes.find((node) => node.id === activeId);

  // Set of node IDs connected to the currently active node
  const connectedNodeIds = useMemo(() => {
    if (!activeId) return new Set<string>();
    const ids = new Set<string>([activeId]);
    connections.forEach((conn) => {
      if (conn.from === activeId) ids.add(conn.to);
      if (conn.to === activeId) ids.add(conn.from);
    });
    return ids;
  }, [activeId, connections]);

  // WATA: what the traced node calls, and what calls it, said in the status bar.
  const nameOf = (id: string) => positionedNodes.find((node) => node.id === id)?.label ?? id;
  const needs = activeId ? connections.filter((conn) => conn.from === activeId).map((conn) => nameOf(conn.to)) : [];
  const neededBy = activeId ? connections.filter((conn) => conn.to === activeId).map((conn) => nameOf(conn.from)) : [];

  const handleHover = (id: string | null) => {
    setHoveredNodeId(id);
    if (onNodeHover) onNodeHover(id);
  };

  const handleSelect = (id: string) => {
    setSelectedNodeId(id === selectedNodeId ? null : id);
    if (onNodeSelect) onNodeSelect(id);
  };

  const radius = nodeSize / 2;

  return (
    <div
      role="region"
      aria-label={label}
      // WATA: each colour has a light value in front of EasyUI's own, kept behind `dark:`; as tall as its canvas.
      className={cn(
        'relative w-full rounded-2xl bg-neutral-50 border border-neutral-200 overflow-hidden select-none flex flex-col dark:bg-[#050505] dark:border-[#1F1F1F]',
        className
      )}
    >
      <svg
        className="w-full h-auto"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* WATA: an arrow at the called end, grey at rest and blue while traced. */}
        <defs>
          <marker id={`${markerId}-idle`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L8 4L0 8Z" fill="var(--chart-muted)" />
          </marker>
          <marker id={`${markerId}-on`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L8 4L0 8Z" fill="#3B82F6" />
          </marker>
        </defs>

        {/* Draw Connection Lines */}
        {connections.map((conn, idx) => {
          const fromNode = positionedNodes.find((n) => n.id === conn.from);
          const toNode = positionedNodes.find((n) => n.id === conn.to);
          if (!fromNode || !toNode || fromNode.x === undefined || fromNode.y === undefined || toNode.x === undefined || toNode.y === undefined) {
            return null;
          }

          const isConnected =
            activeId && (conn.from === activeId || conn.to === activeId);
          const isDimmed = activeId && !isConnected;

          // Bezier curve calculation. WATA: from the edge of one circle to the edge of the other, for the arrow.
          const dx = toNode.x - fromNode.x;
          const dy = toNode.y - fromNode.y;
          const length = Math.hypot(dx, dy) || 1;
          const startX = fromNode.x + (dx / length) * radius;
          const startY = fromNode.y + (dy / length) * radius;
          const endX = toNode.x - (dx / length) * (radius + 3);
          const endY = toNode.y - (dy / length) * (radius + 3);
          const midX = (startX + endX) / 2;
          const midY = (startY + endY) / 2;
          const pathD = `M ${startX} ${startY} Q ${midX} ${midY - 15} ${endX} ${endY}`;

          return (
            <g key={`${conn.from}-${conn.to}-${idx}`}>
              {/* Glow background line when active */}
              {isConnected && (
                <motion.path
                  d={pathD}
                  fill="none"
                  stroke="rgba(59, 130, 246, 0.3)"
                  strokeWidth="6"
                  strokeLinecap="round"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.2 }}
                />
              )}

              {/* Main SVG path */}
              <motion.path
                d={pathD}
                fill="none"
                className={isConnected ? 'stroke-[#3B82F6]' : 'stroke-neutral-300 dark:stroke-[#2A2A2A]'}
                strokeWidth={isConnected ? '2.5' : '1.5'}
                strokeDasharray={isConnected ? 'none' : '4 4'}
                strokeLinecap="round"
                markerEnd={`url(#${markerId}-${isConnected ? 'on' : 'idle'})`}
                animate={{
                  opacity: isDimmed ? 0.2 : isConnected ? 1 : 0.7,
                }}
                transition={motionTransitions.springSnappy}
              />

              {/* WATA: the link's name while traced. */}
              {isConnected && conn.label && (
                <text
                  x={midX}
                  y={midY - 10}
                  textAnchor="middle"
                  className="text-[9px] font-mono fill-neutral-700 dark:fill-[#D4D4D4] pointer-events-none"
                  style={{ paintOrder: 'stroke', strokeWidth: 3, strokeLinejoin: 'round' }}
                  stroke="var(--surface)"
                >
                  {conn.label}
                </text>
              )}
            </g>
          );
        })}

        {/* Draw Nodes */}
        {positionedNodes.map((node) => {
          if (node.x === undefined || node.y === undefined) return null;
          const isCurrentActive = activeId === node.id;
          const isConnected = connectedNodeIds.has(node.id);
          const isDimmed = activeId !== null && !isConnected;
          const said = node.statusLabel ? `${node.label}: ${node.statusLabel}` : node.label;

          return (
            <g
              key={node.id}
              transform={`translate(${node.x}, ${node.y})`}
              onClick={() => handleSelect(node.id)}
              onMouseEnter={() => handleHover(node.id)}
              onMouseLeave={() => handleHover(null)}
              // WATA: reached and traced by the keyboard too; Enter or Space keeps the trace, as a click does.
              tabIndex={0}
              role="button"
              aria-pressed={selectedNodeId === node.id}
              aria-label={said}
              onFocus={() => handleHover(node.id)}
              onBlur={() => handleHover(null)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  handleSelect(node.id);
                }
              }}
              // The trace it starts (the glow, the status bar) is where the focus is.
              className="cursor-pointer outline-none"
            >
              {/* Outer glow ring on active */}
              {isCurrentActive && (
                <motion.circle
                  r={radius + 6}
                  fill="none"
                  stroke="rgba(59, 130, 246, 0.4)"
                  strokeWidth="2"
                  animate={{ scale: [1, 1.15, 1], opacity: [0.6, 0.2, 0.6] }}
                  transition={{ repeat: Infinity, duration: 2 }}
                />
              )}

              {/* Node Circle Background. WATA: its ring says its health. */}
              <motion.circle
                r={radius}
                className={isCurrentActive ? 'fill-neutral-100 dark:fill-[#141414]' : 'fill-white dark:fill-[#0E0E0E]'}
                stroke={node.status ? STATUS_STROKE[node.status] : isCurrentActive ? '#3B82F6' : 'var(--chart-axis)'}
                strokeWidth={node.status ? 2.5 : 1.5}
                animate={{
                  scale: isCurrentActive ? 1.15 : 1,
                  opacity: isDimmed ? 0.25 : 1,
                }}
                transition={motionTransitions.springSnappy}
              />

              {/* WATA: the health's icon in the circle, its name under it. */}
              <motion.text
                textAnchor="middle"
                dy="4"
                className="text-[12px] font-semibold pointer-events-none fill-neutral-700 dark:fill-[#E5E5E5]"
                animate={{ opacity: isDimmed ? 0.25 : 1 }}
              >
                {node.status ? STATUS_ICON[node.status] : '·'}
              </motion.text>
              <motion.text
                textAnchor="middle"
                y={radius + 13}
                className="text-[10px] font-medium pointer-events-none fill-neutral-900 dark:fill-[#FAFAFA]"
                animate={{
                  opacity: isDimmed ? 0.25 : 1,
                  fontWeight: isCurrentActive ? 700 : 500,
                }}
              >
                {node.label}
              </motion.text>
            </g>
          );
        })}
      </svg>

      {/* Floating Status Bar. WATA: what the traced node is and links to, in words. */}
      <div
        aria-live="polite"
        className="m-3 mt-0 flex items-center justify-between gap-3 text-[11px] text-neutral-600 bg-white/90 backdrop-blur px-3 py-1.5 rounded-lg border border-neutral-200 pointer-events-none dark:text-[#A1A1A1] dark:bg-[#0E0E0E]/90 dark:border-[#1F1F1F]"
      >
        <span className="min-w-0">
          {activeNode ? (
            <>
              <span className="font-medium text-neutral-900 dark:text-[#FAFAFA]">{activeNode.label}</span>
              {activeNode.statusLabel && `: ${activeNode.statusLabel}`}
              {activeNode.detail && ` (${activeNode.detail})`}
              {needs.length > 0 && ` · calls ${needs.join(', ')}`}
              {neededBy.length > 0 && ` · called by ${neededBy.join(', ')}`}
            </>
          ) : (
            'Hover or focus a node to trace what it calls and what calls it'
          )}
        </span>
        <span className="shrink-0 font-mono">{connections.length} connections</span>
      </div>
    </div>
  );
};
