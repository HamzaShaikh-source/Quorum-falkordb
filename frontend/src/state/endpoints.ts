import { z } from 'zod';
import { api } from '../api/client';
import * as S from '../api/schemas';

export const getMeta = () => api(S.Meta, 'GET', 'meta');
export const getGraph = (asOf?: number) => api(S.GraphResponse, 'GET', 'graph', { query: { as_of: asOf } });
export const getNode = (id: string) => api(S.NodeDetail, 'GET', `node/${encodeURIComponent(id)}`);
export const runStep = (step: string) => api(S.ScenarioAck, 'POST', `scenario/${step}`);
export const getPreview = (id: string) => api(S.BlastPreview, 'GET', `blast/${encodeURIComponent(id)}/preview`);
export const getReport = (id: string) => api(S.BlastReport, 'GET', `blast/${encodeURIComponent(id)}/report`);
export const getHandoffs = () => api(z.array(S.Handoff), 'GET', 'handoffs');
export const reviewHandoff = (id: string, decision: 'approve' | 'reject') =>
  api(S.Handoff, 'POST', `handoffs/${encodeURIComponent(id)}/review`, { body: { decision } });
export const getActions = () => api(z.array(S.GraphNode), 'GET', 'actions');
export const getAgents = () => api(z.array(S.Agent), 'GET', 'agents');
export const getResume = (id: string) => api(S.Resume, 'GET', `agents/${encodeURIComponent(id)}/resume`);
export const getProcedures = () => api(z.array(S.Procedure), 'GET', 'procedures');
export const getPromptDiff = (agent: string) => api(S.PromptDiff, 'GET', 'prompt-diff', { query: { agent } });
export const askBrain = (question: string) => api(S.BrainAnswer, 'POST', 'brain/ask', { body: { question } });
export const getWhoKnows = (topic: string) => api(S.WhoKnows, 'GET', 'who-knows', { query: { topic } });
export const getTrace = (id: string) => api(S.DecisionTrace, 'GET', `decisions/${encodeURIComponent(id)}/trace`);
export const getInvariants = () => api(S.Invariants, 'GET', 'invariants');
export const getCypher = (requestId: string) => api(S.CypherLog, 'GET', 'cypher-log', { query: { request_id: requestId, limit: 50 } });
export const getBench = () => api(S.BenchResults, 'GET', 'bench/results');
export const getDenied = () => api(S.DeniedDemo, 'GET', 'tenants/denied-demo');
export const approveCorrection = (actionId: string) =>
  api(z.any(), 'POST', `actions/${encodeURIComponent(actionId)}/approve-correction`);

