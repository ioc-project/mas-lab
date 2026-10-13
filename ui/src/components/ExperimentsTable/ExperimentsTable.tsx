//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  MaterialReactTable,
  MRT_RowSelectionState,
  MRT_SortingState,
  MRT_VisibilityState,
} from "material-react-table";
import {
  CreateTableInstance,
  EmptyState,
  TableProps,
  Tooltip,
} from "@open-ui-kit/core";
import {
  Delete as DeleteIcon,
  Edit as EditIcon,
  PlayArrow as PlayIcon,
  ClearAll as ClearCacheIcon,
  ContentCopy as CopyIcon,
  Check as CheckIcon,
  Download as DownloadIcon,
} from "@mui/icons-material";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  IconButton,
  ListItemIcon,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import type { ExperimentSummary } from "@/api/apiCalls";
import { GLOBAL_BACKGROUND_COLOR } from "@/common/styles";
import { Tags } from "@/components/Tags/Tags";
import { ScrollableTooltip } from "@/components/ScrollableTooltip";

function CopyNameButton({ name }: { name: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      size="small"
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(name);
        setCopied(true);
        setTimeout(() => setCopied(false), 3000);
      }}
      sx={{ p: 0.25 }}
    >
      {copied ? (
        <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
      ) : (
        <CopyIcon sx={{ fontSize: 14 }} />
      )}
    </IconButton>
  );
}

export interface ExperimentJobStatus {
  jobId: string;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "timeout"
    | "interrupted";
  progress?: number;
  stdout?: string;
  stderr?: string;
}

type MRT_ColumnDefList = TableProps<ExperimentSummary>["columns"];

const experimentJobKey = (row: ExperimentSummary) =>
  row.library ? `${row.library}/${row.name}` : row.name;

interface ExperimentsTableProps {
  data: ExperimentSummary[];
  isLoading: boolean;
  onDelete?: (names: string[]) => void | Promise<void>;
  onEdit?: (name: string) => void;
  onRun?: (name: string) => void;
  onExport?: (name: string) => void;
  onDeleteCache?: (experimentName: string) => void;
  onView?: (experimentName: string) => void;
  runningJobs?: Record<string, ExperimentJobStatus>;
  defaultHiddenColumns?: string[];
  showLibraryColumn?: boolean;
  title?: string;
}

export const ExperimentsTable = ({
  data,
  isLoading,
  onDelete,
  onEdit,
  onRun,
  onExport,
  onDeleteCache,
  onView,
  runningJobs = {},
  defaultHiddenColumns = [],
  showLibraryColumn = false,
  title,
}: ExperimentsTableProps) => {
  const [columnVisibility, setColumnVisibility] = useState<MRT_VisibilityState>(
    {
      ...defaultHiddenColumns.reduce((acc: Record<string, boolean>, column) => {
        acc[column] = false;
        return acc;
      }, {}),
    },
  );
  const [sorting, setSorting] = useState<MRT_SortingState>([
    { id: "name", desc: true },
  ]);
  const [rowSelection, setRowSelection] = useState<MRT_RowSelectionState>({});
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [pendingDeleteNames, setPendingDeleteNames] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);

  const runningJobsRef = useRef(runningJobs);
  runningJobsRef.current = runningJobs;

  const handleRequestDelete = useCallback((names: string[]) => {
    setPendingDeleteNames(names);
    setDeleteDialogOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await onDelete?.(pendingDeleteNames);
      setDeleteDialogOpen(false);
      setPendingDeleteNames([]);
      setRowSelection({});
    } finally {
      setDeleting(false);
    }
  }, [onDelete, pendingDeleteNames]);

  const handleCancelDelete = useCallback(() => {
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
  }, []);

  const columns = useMemo<MRT_ColumnDefList>(
    () => [
      ...(showLibraryColumn
        ? [
            {
              accessorKey: "library",
              header: "Lab",
              size: 140,
              accessorFn: (row: ExperimentSummary) => (
                <Typography variant="body2">{row.library || "—"}</Typography>
              ),
            },
          ]
        : []),
      {
        accessorKey: "name",
        header: "Name",
        size: 250,
        accessorFn: (row) => (
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <CopyNameButton name={row.name} />
            <Tooltip title={row.name} placement="top">
              <Typography
                variant="body2"
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {row.name}
              </Typography>
            </Tooltip>
          </Stack>
        ),
      },
      {
        id: "status",
        header: "Status",
        size: 100,
        accessorFn: (row) => {
          const job = runningJobsRef.current[experimentJobKey(row)];
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
          const job = runningJobsRef.current[experimentJobKey(row)];
          const stdout = job?.stdout;
          if (!stdout) return <Typography variant="body2">—</Typography>;
          // Build a compact tooltip from benchmark stdout by keeping only:
          //  1. Header (name, strategy, counts) — up to the first blank line
          //  2. Per-run status lines, e.g. "✅ [cot] item=s1 run=1 (4857ms)"
          //  3. Footer summary block (delimited by "======" separators)
          // All verbose agent output between status lines is stripped.
          const isRunLine = (l: string) => /\]\s*item=\w+\s+run=/.test(l);
          const isSep = (l: string) => /^={3,}/.test(l);
          const lines = stdout.split("\n");
          const firstSepIdx = lines.findIndex(isSep);
          // Header ends at the first blank line after benchmark settings
          let headerEnd = 0;
          for (let i = 1; i < lines.length; i++) {
            if (lines[i].trim() === "" && lines[i - 1].trim() !== "") {
              headerEnd = i;
              break;
            }
          }
          const kept: string[] = [];
          for (let i = 0; i < lines.length; i++) {
            if (i <= headerEnd) {
              kept.push(lines[i]);
            } else if (isRunLine(lines[i]) || isSep(lines[i])) {
              kept.push(lines[i]);
            } else if (firstSepIdx >= 0 && i > firstSepIdx) {
              kept.push(lines[i]);
            }
          }
          const summary = kept.join("\n").replace(/\n{3,}/g, "\n\n");
          return (
            <ScrollableTooltip title={summary} placement="right">
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
          const job = runningJobsRef.current[experimentJobKey(row)];
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
        accessorKey: "version",
        header: "Version",
        size: 80,
        accessorFn: (row) => (
          <Typography variant="body2">{row.version || "—"}</Typography>
        ),
      },
      {
        accessorKey: "scenarios",
        header: "Scenarios",
        size: 180,
        accessorFn: (row) => (
          <Tags tags={row.scenarios?.map((s) => ({ name: s })) ?? []} />
        ),
      },
      {
        accessorKey: "dataset",
        header: "Dataset",
        size: 150,
        accessorFn: (row) => (
          <Typography variant="body2">{row.dataset || "—"}</Typography>
        ),
      },
    ],
    [runningJobs, showLibraryColumn],
  );

  const enrichedData = useMemo(
    () =>
      data.map((exp) => {
        const job = runningJobs[experimentJobKey(exp)];
        return {
          ...exp,
          _jobVersion: job
            ? `${job.status}-${job.progress ?? 0}-${job.stdout ?? ""}-${job.stderr ?? ""}`
            : "",
        };
      }),
    [data, runningJobs],
  );

  const tableRef = CreateTableInstance({
    data: enrichedData,
    columns,
    isLoading,
    rowCount: data.length,
    title: { label: title ?? "" },
    topToolbarProps: {
      export: { enableExport: false },
    },
    enableRowActions: true,
    enableSorting: true,
    enableColumnResizing: true,
    renderEmptyRowsFallback: () => (
      <EmptyState
        title="No Experiments"
        description="Add an experiment to get started"
      />
    ),
    state: { columnVisibility, sorting, rowSelection },
    onColumnVisibilityChange: setColumnVisibility,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    renderRowActionMenuItems: ({ row, closeMenu }) => {
      const jobStatus = runningJobs[row.original.name];
      const isRunning =
        jobStatus?.status === "pending" || jobStatus?.status === "running";
      return [
        <MenuItem
          key="run"
          disabled={isRunning}
          onClick={() => {
            onRun?.(row.original.name);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <PlayIcon />
          </ListItemIcon>
          {isRunning ? "Running..." : "Run"}
        </MenuItem>,
        <MenuItem
          key="export"
          onClick={() => {
            onExport?.(row.original.name);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <DownloadIcon />
          </ListItemIcon>
          Export
        </MenuItem>,
        <MenuItem
          key="edit"
          onClick={() => {
            onEdit?.(row.original.name);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <EditIcon />
          </ListItemIcon>
          Edit
        </MenuItem>,
        <MenuItem
          key="delete-cache"
          onClick={() => {
            onDeleteCache?.(row.original.name);
            closeMenu();
          }}
        >
          <ListItemIcon>
            <ClearCacheIcon />
          </ListItemIcon>
          Delete Cache
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
            const names = selectedRows.map((row) => row.original.name);
            if (names.length > 0) handleRequestDelete(names);
          }}
        >
          Delete Selected ({selectedRows.length})
        </Button>
      );
    },

    muiTableBodyRowProps: ({ row }) => {
      return {
        onClick: () => onView?.(row.original.name),
        sx: {
          cursor: onView ? "pointer" : "default",
          backgroundColor: GLOBAL_BACKGROUND_COLOR,
          "& > td": {
            backgroundColor: `${GLOBAL_BACKGROUND_COLOR} !important`,
          },
        },
      };
    },
    muiTablePaperProps: {
      sx: {
        padding: 0,
        backgroundColor: GLOBAL_BACKGROUND_COLOR,
        elevation: 0,
      },
    },
    muiTableHeadCellProps: {
      sx: { backgroundColor: GLOBAL_BACKGROUND_COLOR, color: "#ffffff" },
    },
    muiTableBodyCellProps: {
      sx: {
        backgroundColor: GLOBAL_BACKGROUND_COLOR,
        color: "#ffffff",
        height: "40px",
      },
    },
  });

  return (
    <>
      <MaterialReactTable table={tableRef} />
      <Dialog open={deleteDialogOpen} onClose={handleCancelDelete}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {pendingDeleteNames.length === 1
              ? `Are you sure you want to delete "${pendingDeleteNames[0]}"?`
              : `Are you sure you want to delete ${pendingDeleteNames.length} experiments?`}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelDelete} disabled={deleting}>
            Cancel
          </Button>
          <Button
            variant="primary"
            color="negative"
            onClick={handleConfirmDelete}
            disabled={deleting}
          >
            {deleting ? "Deleting..." : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};
