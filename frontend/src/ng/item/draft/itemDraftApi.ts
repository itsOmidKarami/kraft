import { jsonBody, request, type Answer } from "../../http";
import type { DraftView, Op, Refusal } from "./types";

const base = (id: string) => `/work-items/${encodeURIComponent(id)}/draft`;

/** One function per route (docsite "Item drafts"); each keeps the status and body. */
export const getDraft = (id: string): Promise<Answer<DraftView | Refusal>> => request<DraftView>(base(id));
export const putDraft = (id: string, ops: Op[]): Promise<Answer<DraftView | Refusal>> => request<DraftView>(base(id), jsonBody("PUT", { ops }));
export const discardDraft = (id: string): Promise<Answer<undefined>> => request<undefined>(base(id), jsonBody("DELETE"));
export const applyDraft = (id: string): Promise<Answer<DraftView | Refusal>> => request<DraftView>(`${base(id)}/apply`, jsonBody("POST"));
