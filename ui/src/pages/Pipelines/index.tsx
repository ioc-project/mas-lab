//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle } from "@/components";
import {
  MaterialReactTable,
  MRT_RowSelectionState,
  MRT_SortingState,
} from "material-react-table";
import {
  CreateTableInstance,
  EmptyState,
  TableProps,
  Tooltip,
} from "@open-ui-kit/core";
import {
  Delete as DeleteIcon,
  PlayArrow as PlayIcon,
} from "@mui/icons-material";
import {
  Alert,
  Box,
  Button,
  Stack,
  Typography,
  useTheme,
  ListItemIcon,
  MenuItem,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from "@mui/material";
import { useNavigate } from "react-router";
import { useScopeId, scopeHref } from "@/api/scope";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  usePipelines,
  deletePipeline,
  runPipeline,
  pollJob,
} from "@/api/apiCalls";
import type { PipelineSummary } from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { CreateItemDialog } from "@/components/CreateItemDialog";
import { fetchRuns } from "@/api/runs";
import { ScrollableTooltip } from "@/components/ScrollableTooltip";

const TABLE_BG = "transparent";

interface PipelineJobStatus {
  jobId: string;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "timeout"
    | "interrupted";
  stdout?: string;
  stderr?: string;
}

type PipelineColumnDefs = TableProps<PipelineSummary>["columns"];

const POLL_INTERVAL = 2000;

const Pipelines = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const library = useScopeId();
  const queryClient = useQueryClient();

  const { data: pipelines, isLoading } = usePipelines(library);

  const [createOpen, setCreateOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [pendingDeleteNames, setPendingDeleteNames] = useState<string[]>([]);
  const [rowSelection, setRowSelection] = useState<MRT_RowSelectionState>({});
  const [sorting, setSorting] = useState<MRT_SortingState>([
    { id: "name", desc: false },
  ]);
  const [runningJobs, setRunningJobs] = useState<
    Record<string, PipelineJobStatus>
  >({});
  const [alertMessage, setAlertMessage] = useState<{
    severity: "success" | "error";
    message: string;
  } | null>(null);
  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const runningJobsRef = useRef(runningJobs);
  runningJobsRef.current = runningJobs;

  const rows = useMemo<PipelineSummary[]>(() => pipelines ?? [], [pipelines]);

  useEffect(() => {
    if (!alertMessage) return;
    const timer = setTimeout(() => setAlertMessage(null), 5000);
    return () => clearTimeout(timer);
  }, [alertMessage]);

  const startPolling = useCallback((pipelineName: string, jobId: string) => {
    const poll = async () => {
      try {
        const job = await pollJob(jobId);
        const isTerminal =
          job.status === "completed" ||
          job.status === "failed" ||
          job.status === "cancelled" ||
          job.status === "timeout";

        setRunningJobs((prev) => ({
          ...prev,
          [pipelineName]: {
            jobId,
            status: job.status,
            stdout: job.stdout || undefined,
            stderr: job.stderr || undefined,
          },
        }));

        if (isTerminal) {
          delete pollTimers.current[pipelineName];
          queryClient.invalidateQueries({ queryKey: ["experiments"] });
          queryClient.invalidateQueries({ queryKey: ["experiment"] });
        } else {
          pollTimers.current[pipelineName] = setTimeout(poll, POLL_INTERVAL);
        }
      } catch {
        delete pollTimers.current[pipelineName];
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[pipelineName];
          return next;
        });
      }
    };

    pollTimers.current[pipelineName] = setTimeout(poll, POLL_INTERVAL);
  }, []);

  useEffect(() => {
    const recoverJobs = async () => {
      try {
        const runs = (await fetchRuns()).filter((r) => r.kind === "pipeline");
        const seen = new Set<string>();
        for (const run of runs) {
          const pipelineName = run.item;
          if (!pipelineName || seen.has(pipelineName)) continue;
          seen.add(pipelineName);
          const isTerminal =
            run.status === "completed" ||
            run.status === "failed" ||
            run.status === "cancelled" ||
            run.status === "timeout";
          if (isTerminal) {
            setRunningJobs((prev) => ({
              ...prev,
              [pipelineName]: {
                jobId: run.id,
                status: run.status as PipelineJobStatus["status"],
              },
            }));
          } else if (!pollTimers.current[pipelineName]) {
            setRunningJobs((prev) => ({
              ...prev,
              [pipelineName]: {
                jobId: run.id,
                status: run.status as PipelineJobStatus["status"],
              },
            }));
            startPolling(pipelineName, run.id);
          }
        }
      } catch {
        /* server may be unreachable */
      }
    };

    if (library) recoverJobs();
    const timer = window.setInterval(() => {
      if (library) void recoverJobs();
    }, POLL_INTERVAL);
    return () => window.clearInterval(timer);
  }, [library, startPolling]);

  const handleRun = useCallback(
    async (name: string) => {
      setRunningJobs((prev) => ({
        ...prev,
        [name]: { jobId: "", status: "pending" },
      }));

      try {
        const { job_id } = await runPipeline(library, name);
        setRunningJobs((prev) => ({
          ...prev,
          [name]: { jobId: job_id, status: "running" },
        }));
        startPolling(name, job_id);
      } catch (err) {
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[name];
          return next;
        });
        setAlertMessage({
          severity: "error",
          message:
            err instanceof Error
              ? err.message
              : `Failed to run pipeline "${name}"`,
        });
      }
    },
    [library, startPolling],
  );

  const columns = useMemo<PipelineColumnDefs>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        size: 200,
        accessorFn: (row) => (
          <Tooltip title={row.name} placement="top">
            <Typography
              variant="body2"
              sx={{
                cursor: "pointer",
                "&:hover": { textDecoration: "underline" },
                textOverflow: "ellipsis",
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
              onClick={() =>
                navigate(
                  scopeHref(library, `/pipelines/${encodeURIComponent(row.name)}`),
                )
              }
            >
              {row.name}
            </Typography>
          </Tooltip>
        ),
      },
      {
        accessorKey: "description",
        header: "Description",
        size: 300,
        accessorFn: (row) => (
          <Tooltip title={row.description} placement="top">
            <Typography
              variant="body2"
              sx={{
                textOverflow: "ellipsis",
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
            >
              {row.description || "—"}
            </Typography>
          </Tooltip>
        ),
      },
      {
        accessorKey: "experiment",
        header: "Experiment",
        size: 180,
        accessorFn: (row) => row.experiment || "—",
      },
      {
        id: "step_count",
        header: "Steps",
        size: 80,
        accessorFn: (row) => row.steps?.length ?? 0,
      },
      {
        id: "status",
        header: "Status",
        size: 100,
        accessorFn: (row) => {
          const job = runningJobsRef.current[row.name];
          if (!job) {
            return (
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                Inactive
              </Typography>
            );
          }
          if (job.status === "completed") {
            return (
              <Typography variant="body2" sx={{ color: "success.main" }}>
                Completed
              </Typography>
            );
          }
          if (job.status === "failed") {
            return (
              <Typography variant="body2" sx={{ color: "error.main" }}>
                Failed
              </Typography>
            );
          }
          if (job.status === "cancelled" || job.status === "timeout") {
            return (
              <Typography variant="body2" sx={{ color: "warning.main" }}>
                {job.status === "cancelled" ? "Cancelled" : "Timeout"}
              </Typography>
            );
          }
          return (
            <Typography variant="body2" sx={{ color: "primary.main" }}>
              Running
            </Typography>
          );
        },
      },
      {
        id: "output",
        header: "Output",
        size: 200,
        accessorFn: (row) => {
          const job = runningJobsRef.current[row.name];
          const stdout = job?.stdout;
          if (!stdout) return <Typography variant="body2">—</Typography>;
          return (
            <ScrollableTooltip title={stdout} placement="right">
              <Typography
                variant="body2"
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {stdout}
              </Typography>
            </ScrollableTooltip>
          );
        },
      },
      {
        id: "errors",
        header: "Errors",
        size: 150,
        accessorFn: (row) => {
          const job = runningJobsRef.current[row.name];
          const stderr = job?.stderr;
          if (!stderr) return <Typography variant="body2">—</Typography>;
          return (
            <ScrollableTooltip title={stderr} placement="right">
              <Typography
                variant="body2"
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  color: "error.main",
                }}
              >
                {stderr}
              </Typography>
            </ScrollableTooltip>
          );
        },
      },
    ],
    [library, navigate, runningJobs],
  );

  const enrichedData = useMemo(
    () =>
      rows.map((p) => {
        const job = runningJobs[p.name];
        return {
          ...p,
          _jobVersion: job
            ? `${job.status}-${job.stdout ?? ""}-${job.stderr ?? ""}`
            : "",
        };
      }),
    [rows, runningJobs],
  );

  const handleRequestDelete = useCallback((names: string[]) => {
    setPendingDeleteNames(names);
    setDeleteDialogOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    try {
      await Promise.all(
        pendingDeleteNames.map((n) => deletePipeline(library, n)),
      );
      queryClient.invalidateQueries({ queryKey: ["pipelines", library] });
      setRowSelection({});
    } catch {
      // silently fail
    }
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
  }, [pendingDeleteNames, library, queryClient]);

  const handleCancelDelete = useCallback(() => {
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
  }, []);

  const tableRef = CreateTableInstance({
    data: enrichedData,
    columns,
    isLoading,
    rowCount: rows.length,
    title: { label: "" },
    topToolbarProps: {
      export: { enableExport: false },
    },
    enableRowActions: true,
    enableSorting: true,
    enableColumnResizing: true,
    renderEmptyRowsFallback: () => (
      <EmptyState
        title="No Pipelines"
        description="Add a pipeline to get started"
      />
    ),
    state: { sorting, rowSelection },
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    renderRowActionMenuItems: ({ row, closeMenu }) => {
      const job = runningJobs[row.original.name];
      const isRunning = job?.status === "pending" || job?.status === "running";
      return [
        <MenuItem
          key="run"
          disabled={isRunning}
          onClick={() => {
            handleRun(row.original.name);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <PlayIcon />
          </ListItemIcon>
          {isRunning ? "Running..." : "Run"}
        </MenuItem>,
        <MenuItem
          key="delete"
          onClick={() => {
            handleRequestDelete([row.original.name]);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <DeleteIcon />
          </ListItemIcon>
          Delete
        </MenuItem>,
      ];
    },
    renderToolbarInternalActions: ({ table }) => {
      const selectedRows = table.getSelectedRowModel().rows;
      const hasSelection = selectedRows.length > 0;
      return (
        <Button
          variant="primary"
          color="negative"
          disabled={!hasSelection}
          onClick={() => {
            const names = selectedRows.map((r) => r.original.name);
            if (names.length > 0) handleRequestDelete(names);
          }}
        >
          Delete Selected ({selectedRows.length})
        </Button>
      );
    },
    muiTableBodyRowProps: () => ({
      sx: {
        cursor: "pointer",
        backgroundColor: TABLE_BG,
        "& > td": {
          backgroundColor: `${TABLE_BG} !important`,
        },
      },
    }),
    muiTablePaperProps: {
      sx: {
        padding: 0,
        backgroundColor: TABLE_BG,
        elevation: 0,
      },
    },
    muiTableHeadCellProps: {
      sx: { backgroundColor: TABLE_BG, color: "#ffffff" },
    },
    muiTableBodyCellProps: {
      sx: {
        backgroundColor: TABLE_BG,
        color: "#ffffff",
        height: "40px",
      },
    },
  });

  return (
    <Box sx={{ position: "relative" }}>
      {alertMessage && (
        <Alert
          severity={alertMessage.severity}
          onClose={() => setAlertMessage(null)}
          sx={{
            whiteSpace: "pre-wrap",
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 1000,
          }}
        >
          {alertMessage.message}
        </Alert>
      )}
      <PageWithTitle
        title={
          <Stack
            direction="row"
            sx={{
              alignItems: "center",
              justifyContent: "space-between",
              width: "100%",
            }}
          >
            <Typography
              variant="h5"
              sx={{
                color: theme.palette.vars.interactivePrimaryDefaultDefault,
              }}
            >
              Pipelines
            </Typography>
            <Button onClick={() => setCreateOpen(true)}>Add Pipeline</Button>
          </Stack>
        }
      >
        <Stack direction="column" sx={{ gap: "24px" }}>
          <MaterialReactTable table={tableRef} />
        </Stack>
      </PageWithTitle>

      <Dialog open={deleteDialogOpen} onClose={handleCancelDelete}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {pendingDeleteNames.length === 1
              ? `Are you sure you want to delete "${pendingDeleteNames[0]}"?`
              : `Are you sure you want to delete ${pendingDeleteNames.length} pipelines?`}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelDelete}>Cancel</Button>
          <Button
            variant="primary"
            color="negative"
            onClick={handleConfirmDelete}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
      <CreateItemDialog
        open={createOpen}
        kind="pipeline"
        scopeId={library}
        onClose={() => setCreateOpen(false)}
        onCreated={(sid, id) =>
          navigate(scopeHref(sid, `/pipelines/${encodeURIComponent(id)}`))
        }
      />
    </Box>
  );
};

export default Pipelines;
