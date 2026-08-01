/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * CAS Management API for browsing CAS servers, caslibs and in-memory tables.
 * The Optimize panel's dataset picker builds its cascading server → caslib →
 * table dropdowns from these listings (the same interactive-selection pattern
 * the portal framework's prompt controls use for compute libraries), and
 * validates the chosen table before launching the optimization job.
 * casManagement browses without an explicit CAS session, and it only lists
 * LOADED tables — exactly the ones the job's compute session can read.
 */

import { viyaGet } from './http-client';

interface CasCollection<T> {
  items?: T[];
}

interface NamedItem {
  name?: string;
}

/** List the CAS servers of the deployment (usually just cas-shared-default). */
export async function getCasServers(): Promise<string[]> {
  const data = await viyaGet<CasCollection<NamedItem>>('/casManagement/servers?limit=100');
  return (data.items ?? []).map((item) => String(item.name ?? '')).filter(Boolean);
}

/** List the caslibs of a CAS server. */
export async function getCaslibs(server: string): Promise<string[]> {
  const data = await viyaGet<CasCollection<NamedItem>>(
    `/casManagement/servers/${encodeURIComponent(server)}/caslibs?limit=500&excludeItemLinks=true`
  );
  return (data.items ?? []).map((item) => String(item.name ?? '')).filter(Boolean);
}

/** List the loaded (in-memory) tables of a caslib. */
export async function getCasTables(server: string, caslib: string): Promise<string[]> {
  const data = await viyaGet<CasCollection<NamedItem>>(
    `/casManagement/servers/${encodeURIComponent(server)}/caslibs/${encodeURIComponent(caslib)}` +
      '/tables?limit=1000&excludeItemLinks=true'
  );
  return (data.items ?? []).map((item) => String(item.name ?? '')).filter(Boolean);
}

/** What the optimize panel needs to validate a CAS dataset table up front. */
export interface CasTableInfo {
  columns: string[];
  rowCount: number;
}

/**
 * Probe a CAS dataset table — the Optimize panel validates the table's
 * columns against the prompt's variables (plus the response column) and its
 * row count against the sample minimum BEFORE launching the job. Throws on
 * an unknown caslib/table; the caller turns that into a toast.
 */
export async function getCasTableInfo(
  caslib: string,
  table: string,
  server = 'cas-shared-default'
): Promise<CasTableInfo> {
  const base =
    `/casManagement/servers/${encodeURIComponent(server)}` +
    `/caslibs/${encodeURIComponent(caslib)}/tables/${encodeURIComponent(table)}`;
  const info = await viyaGet<{ rowCount?: number }>(base);
  const columns = await viyaGet<{ items?: { name?: string }[] }>(`${base}/columns?limit=1000`);
  return {
    columns: (columns.items ?? []).map((column) => String(column.name ?? '')).filter(Boolean),
    rowCount: Number(info.rowCount ?? 0),
  };
}

/** One page of a loaded CAS table, as column names + row value arrays. */
export interface CasTableRows {
  columns: string[];
  rows: unknown[][];
}

/**
 * Read rows of a LOADED (promoted) CAS table — the RAG Builder's ledger
 * browser. casManagement only serves in-memory tables, which is exactly the
 * promoted ledger's state; a table that exists only as a saved file answers
 * 404 (the caller shows a hint). Cell order follows the columns listing.
 */
export async function getCasTableRows(
  caslib: string,
  table: string,
  server = 'cas-shared-default',
  limit = 500
): Promise<CasTableRows> {
  // Two DIFFERENT services, and the split is not cosmetic: casManagement
  // describes a table (its columns, its metadata) but has no row handler at
  // all - `/tables/<t>/rows` answers 404 "no handler defined for the path",
  // which reads exactly like a missing table and sent every ledger read down
  // the "no such table" branch. Rows come from casRowSets.
  const path =
    `/servers/${encodeURIComponent(server)}` +
    `/caslibs/${encodeURIComponent(caslib)}/tables/${encodeURIComponent(table)}`;
  const columns = await viyaGet<{ items?: { name?: string }[] }>(
    `/casManagement${path}/columns?limit=1000`
  );
  const rows = await viyaGet<{ items?: { cells?: unknown[] }[] }>(
    `/casRowSets${path}/rows?limit=${limit}`
  );
  return {
    columns: (columns.items ?? []).map((column) => String(column.name ?? '')).filter(Boolean),
    rows: (rows.items ?? []).map((row) => row.cells ?? []),
  };
}
