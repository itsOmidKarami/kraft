import { jsonBody, request, type Answer } from "../../http";
import type { Area, DraftSummary, DraftView, Op, OpsView } from "./types";

/** W9's draft routes (docsite "Drafts"). Each answers `{status, body}`, so a
 *  caller can read a 409's diff or a 422's op index (R44). */
const base = (area: Area, key: string) => `/drafts/${area}/${encodeURIComponent(key)}`;

export const listDrafts = () => request<DraftSummary[]>("/drafts");
export const getDraft = (area: Area, key: string) => request<DraftView>(base(area, key));
export const putFile = (area: Area, key: string, file: string, text: string) =>
  request<DraftView>(`${base(area, key)}/files/${file.split("/").map(encodeURIComponent).join("/")}`, jsonBody("PUT", { text }));
export const postOps = (area: Area, key: string, ops: Op[], preview = false) =>
  request<OpsView>(`${base(area, key)}/ops${preview ? "?preview=1" : ""}`, jsonBody("POST", { ops }));
export const undo = (area: Area, key: string) => request<DraftView>(`${base(area, key)}/undo`, jsonBody("POST"));
export const publish = (area: Area, key: string) => request<{ published: string[] }>(`${base(area, key)}/publish`, jsonBody("POST"));
export const discard = (area: Area, key: string) => request<void>(base(area, key), jsonBody("DELETE"));
/** The draft keeps its text and takes the files' current digests as its base,
 *  after the person has seen the 409's diff (R45). */
export const rebase = (area: Area, key: string) => request<DraftView>(`${base(area, key)}/rebase`, jsonBody("POST"));
/** One component's YAML as the server writes it (R46). */
export const fragment = (area: Area, key: string, path: string) =>
  request<{ path: string; text: string }>(`${base(area, key)}/fragment?path=${encodeURIComponent(path)}`);

export const ok = <T>(a: Answer<T>): a is Answer<T> => a.status >= 200 && a.status < 300;

/** Every draft write says so, for the sidebar's dots (brief Decided 10). */
export const DRAFTS_CHANGED = "kraft:drafts-changed";
export const draftsChanged = () => window.dispatchEvent(new CustomEvent(DRAFTS_CHANGED));
