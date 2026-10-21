//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useLocation } from "react-router";
import { useScopeId } from "@/api/scope";
import { useDeleteRun, useRun, useRuns } from "@/api/runs";
import {
  Box,
  CircularProgress,
  MenuItem,
  Select,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import type { SelectChangeEvent } from "@mui/material";
import { PageWithTitle, CodeBlock, TabPanel } from "@/components";
import { useRuntimeRunners } from "@/api/apiCalls";
import { useScopeConfig } from "@/api/scopes";
import { Button, Tab, Tabs } from "@open-ui-kit/core";

const TAB_KEYS = ["infra", "flavours", "workspace"] as const;
type TabKey = (typeof TAB_KEYS)[number];

const TAB_LABELS: Record<TabKey, string> = {
  infra: "Infra",
  flavours: "Flavours",
  workspace: "Workspace",
};

const ControlPanel = () => {
  const theme = useTheme();
  const location = useLocation();
  const library = useScopeId();
  void location;

  const { data: configFiles, isLoading } = useScopeConfig(library);
  const { data: runs = [] } = useRuns();
  const deleteRun = useDeleteRun();
  const { data: runtimeRunners } = useRuntimeRunners();
  const [selectedRunId, setSelectedRunId] = useState("");
  const { data: selectedRun } = useRun(selectedRunId);
  const [activeTab, setActiveTab] = useState(0);
  const [selectedFiles, setSelectedFiles] = useState<Record<TabKey, string>>(
    {} as Record<TabKey, string>,
  );

  const handleTabChange = (_: React.SyntheticEvent, newValue: number) => {
    setActiveTab(newValue);
  };

  const getFilesForTab = (tab: TabKey): Record<string, string> => {
    if (!configFiles) return {};
    return configFiles[tab] ?? {};
  };

  const getSelectedFile = (tab: TabKey): string => {
    const files = getFilesForTab(tab);
    const fileNames = Object.keys(files);
    if (fileNames.length === 0) return "";
    return selectedFiles[tab] ?? fileNames[0];
  };

  const handleFileChange = (tab: TabKey, event: SelectChangeEvent<string>) => {
    setSelectedFiles((prev) => ({ ...prev, [tab]: event.target.value }));
  };

  if (isLoading) {
    return (
      <PageWithTitle title="Control Panel">
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flex: 1,
          }}
        >
          <CircularProgress />
        </Box>
      </PageWithTitle>
    );
  }

  return (
    <PageWithTitle
      title={
        <Typography
          variant={"h5"}
          sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
        >
          Control Panel
        </Typography>
      }
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
      >
        {runtimeRunners && runtimeRunners.length > 0 && (
          <Typography variant="body2" sx={{ mb: 1, opacity: 0.85 }}>
            Runtime runners:{" "}
            {runtimeRunners.map((r) => r.id).join(", ")}
          </Typography>
        )}
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Runs
          </Typography>
          {runs.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No runs yet.
            </Typography>
          ) : (
            runs.map((run) => (
              <Stack key={run.id} direction="row" spacing={2} sx={{ mb: 0.5 }} alignItems="center">
                <Button size="small" onClick={() => setSelectedRunId(run.id)}>
                  {run.item} · {run.status}
                  {run.request_summary?.source ? ` · ${run.request_summary.source}` : ""}
                </Button>
                <Button size="small" onClick={() => deleteRun.mutate(run.id)}>
                  Delete
                </Button>
              </Stack>
            ))
          )}
          {selectedRun && (
            <Box sx={{ mt: 1, mb: 2 }}>
              <Typography variant="caption" color="text.secondary">
                {selectedRun.scope} / {selectedRun.item} · {selectedRun.status}
              </Typography>
              <CodeBlock code={selectedRun.log || "(no output yet)"} language="text" />
            </Box>
          )}
        </Box>
        <Tabs value={activeTab} onChange={handleTabChange}>
          {TAB_KEYS.map((key, idx) => (
            <Tab
              key={key}
              label={TAB_LABELS[key]}
              id={`control-panel-tab-${idx}`}
            />
          ))}
        </Tabs>

        {TAB_KEYS.map((tabKey, idx) => {
          const files = getFilesForTab(tabKey);
          const fileNames = Object.keys(files);
          const selected = getSelectedFile(tabKey);

          return (
            <TabPanel
              key={tabKey}
              value={activeTab}
              index={idx}
              sx={{ flex: 1, overflow: "auto" }}
            >
              {fileNames.length === 0 ? (
                <Typography
                  variant="body2"
                  sx={{ color: "text.secondary", mt: 4, textAlign: "center" }}
                >
                  No {TAB_LABELS[tabKey].toLowerCase()} configuration files
                  available.
                </Typography>
              ) : (
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                    mt: 2,
                  }}
                >
                  <Select
                    size="small"
                    value={selected}
                    onChange={(e) => handleFileChange(tabKey, e)}
                    sx={{ maxWidth: 400 }}
                  >
                    {fileNames.map((name) => (
                      <MenuItem key={name} value={name}>
                        {name}
                      </MenuItem>
                    ))}
                  </Select>
                  {selected && files[selected] && (
                    <CodeBlock code={files[selected]} language="yaml" />
                  )}
                </Box>
              )}
            </TabPanel>
          );
        })}
      </Box>
    </PageWithTitle>
  );
};

export default ControlPanel;
