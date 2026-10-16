/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type SessionEvent = {
  type: "session";
  session_id: string;
};

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
};

export type ReplyEvent = {
  type: "message";
  intent: "clarify" | "out_of_scope";
  message: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

export type AgentEvent = SessionEvent | ProgressEvent | ResultEvent | ReplyEvent | ErrorEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  error?: string;
};
