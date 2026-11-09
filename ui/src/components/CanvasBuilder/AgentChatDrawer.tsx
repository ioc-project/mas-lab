//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState, useCallback, useRef, useEffect, useEffectEvent } from "react";
import { parse, stringify } from "yaml";
import { Drawer } from "@mui/material";
import {
  Box,
  Typography,
  IconButton,
  Icons,
  InputField,
  Stack,
  Spinner,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  Tooltip,
} from "@open-ui-kit/core";
import { runAgent, pollJob } from "@/api/apiCalls";
import { PromptsModal, addPromptToStore } from "./PromptsModal";

import type { ChatMessage } from "./types";

interface AgentChatDrawerProps {
  open: boolean;
  onClose: () => void;
  agentName: string;
  agentYaml: string;
  library: string;
  initialMessages?: ChatMessage[];
  onMessagesChange?: (messages: ChatMessage[]) => void;
}

const POLL_INTERVAL = 1500;

export function AgentChatDrawer({
  open,
  onClose,
  agentName,
  agentYaml,
  library,
  initialMessages = [],
  onMessagesChange,
}: AgentChatDrawerProps) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [promptsModalOpen, setPromptsModalOpen] = useState(false);
  const [pendingSaveText, setPendingSaveText] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const sessionIdRef = useRef<string>(crypto.randomUUID());

  const resetOnOpenChange = useEffectEvent(() => {
    if (open) {
      setMessages(initialMessages);
    } else {
      setInput("");
    }
  });

  useEffect(() => {
    resetOnOpenChange();
  }, [open]);

  const reportMessages = useEffectEvent(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    onMessagesChange?.(messages);
  });

  useEffect(() => {
    reportMessages();
  }, [messages]);

  const buildManifestWithHistory = useCallback(
    (currentMessages: ChatMessage[]) => {
      if (!agentYaml) return agentYaml;
      if (currentMessages.length === 0) return agentYaml;

      const doc = parse(agentYaml);
      if (!doc?.spec) return agentYaml;

      const historyLines = currentMessages
        .filter((m) => m.role !== "error")
        .map(
          (m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.content}`,
        );
      const historyText = `Previous conversation:\n${historyLines.join("\n")}`;

      doc.spec.context = {
        ...(doc.spec.context ?? {}),
        conversation_history: historyText,
      };

      return stringify(doc);
    },
    [agentYaml],
  );

  const handleSend = useCallback(async () => {
    const query = input.trim();
    if (!query || isLoading) return;

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: query,
    };

    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInput("");
    setIsLoading(true);

    try {
      const yaml = buildManifestWithHistory(updatedMessages);
      if (!yaml) {
        throw new Error(
          "No agent manifest available. Make sure the agent has a name.",
        );
      }
      const submit = await runAgent({
        library,
        manifest_yaml: yaml,
        query,
        session_id: sessionIdRef.current,
        verbose: false,
      });
      if (submit.session_id) {
        sessionIdRef.current = submit.session_id;
      }

      let result = await pollJob(submit.job_id);
      while (result.status === "pending" || result.status === "running") {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        result = await pollJob(submit.job_id);
      }
      if (result.session_id) {
        sessionIdRef.current = result.session_id;
      }

      if (result.status === "completed") {
        const content =
          result.response?.trim() || "Agent completed with no output.";
        setMessages((prev) => [
          ...prev,
          { id: crypto.randomUUID(), role: "assistant", content },
        ]);
      } else {
        const content =
          result.error_message?.trim() ||
          result.error?.trim() ||
          "Agent execution failed.";
        const detail =
          result.error_detail?.trim() ||
          result.stderr?.trim() ||
          result.stdout?.trim() ||
          undefined;
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "error",
            content,
            detail: detail && detail !== content ? detail : undefined,
          },
        ]);
      }
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "An unexpected error occurred.";
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "error",
          content: errorMsg,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [input, isLoading, messages, buildManifestWithHistory, library]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handlePromptSelect = useCallback((text: string) => {
    setInput(text);
  }, []);

  const handleRequestSavePrompt = useCallback((text: string) => {
    setPendingSaveText(text);
  }, []);

  const handleConfirmSavePrompt = useCallback(() => {
    addPromptToStore(pendingSaveText);
    setPendingSaveText("");
  }, [pendingSaveText]);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: 420,
          display: "flex",
          flexDirection: "column",
          bgcolor: "background.paper",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          py: 1.5,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Chat with {agentName || "Agent"}
        </Typography>
        <Stack direction="row" sx={{ gap: "4px" }}>
          <IconButton
            size="small"
            onClick={() => {
              setMessages([]);
              sessionIdRef.current = crypto.randomUUID();
            }}
            disabled={messages.length === 0 || isLoading}
            title="Clear chat"
          >
            <Icons.Delete fontSize="small" />
          </IconButton>
          <IconButton size="small" onClick={onClose}>
            <Icons.Close fontSize="small" />
          </IconButton>
        </Stack>
      </Box>

      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          px: 2,
          py: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
        }}
      >
        {messages.length === 0 && (
          <Typography
            variant="body2"
            sx={{ color: "text.secondary", textAlign: "center", mt: 4 }}
          >
            Send a message to run the agent.
          </Typography>
        )}
        {messages.map((msg) => (
          <Box
            key={msg.id}
            sx={{
              alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
              display: "flex",
              flexDirection: "column",
              alignItems: msg.role === "user" ? "flex-end" : "flex-start",
              "&:hover .save-prompt-btn": { opacity: 1 },
            }}
          >
            {msg.role === "error" ? (
              <Tooltip
                title={msg.detail || msg.content}
                placement="top-start"
                enterDelay={300}
                slotProps={{
                  tooltip: {
                    sx: {
                      maxWidth: 420,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    },
                  },
                }}
              >
                <Box
                  sx={{
                    px: 1.5,
                    py: 1,
                    borderRadius: 2,
                    border: 1,
                    borderColor: "error.dark",
                    bgcolor: "error.dark",
                    color: "error.contrastText",
                    opacity: 0.92,
                    cursor: msg.detail ? "help" : "default",
                    width: "100%",
                  }}
                >
                  <Stack direction="row" spacing={1} alignItems="flex-start">
                    <Icons.ErrorCircleOutline sx={{ fontSize: 18, mt: 0.25 }} />
                    <Box sx={{ minWidth: 0 }}>
                      <Typography
                        variant="caption"
                        sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
                      >
                        Agent error
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                      >
                        {msg.content}
                      </Typography>
                      {msg.detail && (
                        <Typography
                          variant="caption"
                          sx={{ opacity: 0.8, mt: 0.5, display: "block" }}
                        >
                          Hover for full details
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                </Box>
              </Tooltip>
            ) : (
              <Box
                sx={{
                  px: 1.5,
                  py: 1,
                  borderRadius: 2,
                  bgcolor:
                    msg.role === "user" ? "primary.dark" : "action.hover",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                <Typography variant="body2">{msg.content}</Typography>
              </Box>
            )}
            {msg.role === "user" && (
              <IconButton
                className="save-prompt-btn"
                size="small"
                onClick={() => handleRequestSavePrompt(msg.content)}
                title="Save as prompt"
                sx={{ opacity: 0, transition: "opacity 0.2s", mt: 0.25 }}
              >
                <Icons.PlusCircleOutline sx={{ fontSize: 16 }} />
              </IconButton>
            )}
          </Box>
        ))}
        {isLoading && (
          <Box
            sx={{
              alignSelf: "flex-start",
              display: "flex",
              gap: 1,
              alignItems: "center",
            }}
          >
            <Spinner size={16} />
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              Agent is thinking...
            </Typography>
          </Box>
        )}
        <div ref={messagesEndRef} />
      </Box>

      <Stack
        direction="row"
        sx={{
          padding: "8px",
          borderTop: 1,
          borderColor: "divider",
          gap: 1,
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <InputField
          fullWidth
          multiline
          maxRows={4}
          size="small"
          variant="standard"
          autoComplete="off"
          placeholder="Ask the agent..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          sx={{
            "&.MuiTextField-root .MuiInputBase-root": {
              marginTop: 0,
            },
          }}
        />
        <IconButton
          size="small"
          onClick={() => setPromptsModalOpen(true)}
          title="Saved prompts"
          color="default"
        >
          <Icons.BookmarkFilled fontSize="small" />
        </IconButton>
        <IconButton
          color="primary"
          onClick={handleSend}
          disabled={!input.trim() || isLoading}
          size="small"
        >
          <Icons.Send fontSize="small" />
        </IconButton>
      </Stack>

      <PromptsModal
        open={promptsModalOpen}
        onClose={() => setPromptsModalOpen(false)}
        onSelect={handlePromptSelect}
      />

      <Dialog
        open={pendingSaveText.length > 0}
        onClose={() => setPendingSaveText("")}
      >
        <DialogTitle>Save Prompt</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Add this message to your saved prompts?
          </DialogContentText>
          <Box
            sx={{
              mt: 1,
              p: 1.5,
              borderRadius: 1,
              bgcolor: "action.hover",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 200,
              overflow: "auto",
            }}
          >
            <Typography variant="body2">{pendingSaveText}</Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button variant="gradient" onClick={() => setPendingSaveText("")}>
            Cancel
          </Button>
          <Button variant="gradient" onClick={handleConfirmSavePrompt}>
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  );
}
