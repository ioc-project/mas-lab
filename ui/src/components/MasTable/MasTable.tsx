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
  ContentCopy as DuplicateIcon,
} from "@mui/icons-material";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  ListItemIcon,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import type { MASManifest } from "@/types/mas-types";
import { Tags } from "@/components";

type MRT_ColumnDefList = TableProps<MASManifest>["columns"];

interface MasTableProps {
  data: MASManifest[];
  isLoading: boolean;
  isError?: boolean;
  onReload?: () => void;
  onMasClick?: (mas: MASManifest) => void;
  onDelete?: (names: string[]) => void | Promise<void>;
  onDuplicate?: (params: {
    masName: string;
    description: string;
    intent: string;
    sourceMasName: string;
  }) => Promise<void>;
  defaultHiddenColumns?: string[];
  title?: string;
}

export const MasTable = ({
  data,
  isLoading,
  onReload,
  onMasClick,
  onDelete,
  onDuplicate,
  defaultHiddenColumns = [],
  title,
}: MasTableProps) => {
  const [columnVisibility, setColumnVisibility] = useState<MRT_VisibilityState>(
    {
      id: false,
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

  const [duplicateDialogOpen, setDuplicateDialogOpen] = useState(false);
  const [duplicateName, setDuplicateName] = useState("");
  const [duplicateDescription, setDuplicateDescription] = useState("");
  const [duplicateIntent, setDuplicateIntent] = useState("");
  const [duplicateSourceName, setDuplicateSourceName] = useState("");
  const [duplicateError, setDuplicateError] = useState("");
  const [isDuplicating, setIsDuplicating] = useState(false);
  const [duplicateSuccess, setDuplicateSuccess] = useState("");

  useEffect(() => {
    if (!duplicateSuccess) return;
    const timer = setTimeout(() => setDuplicateSuccess(""), 3000);
    return () => clearTimeout(timer);
  }, [duplicateSuccess]);

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

  const handleOpenDuplicate = useCallback((mas: MASManifest) => {
    setDuplicateName(`${mas.metadata?.name ?? ""} (copy)`);
    setDuplicateDescription(mas.metadata?.description ?? "");
    setDuplicateIntent(mas.intent?.summary ?? "");
    setDuplicateSourceName(mas.metadata?.name ?? "");
    setDuplicateError("");
    setDuplicateDialogOpen(true);
  }, []);

  const handleCloseDuplicate = useCallback(() => {
    setDuplicateDialogOpen(false);
    setDuplicateError("");
  }, []);

  const handleConfirmDuplicate = useCallback(async () => {
    const trimmedName = duplicateName.trim();
    if (!trimmedName) {
      setDuplicateError("MAS name is required.");
      return;
    }
    setIsDuplicating(true);
    try {
      await onDuplicate?.({
        masName: trimmedName,
        description: duplicateDescription.trim(),
        intent: duplicateIntent.trim(),
        sourceMasName: duplicateSourceName,
      });
      setDuplicateDialogOpen(false);
      setDuplicateSuccess(`"${trimmedName}" created successfully.`);
    } catch (err) {
      setDuplicateError(
        err instanceof Error ? err.message : "Failed to duplicate application.",
      );
    } finally {
      setIsDuplicating(false);
    }
  }, [
    duplicateName,
    duplicateDescription,
    duplicateIntent,
    duplicateSourceName,
    onDuplicate,
  ]);

  const columns = useMemo<MRT_ColumnDefList>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        size: 250,
        accessorFn: ({ metadata }) => {
          return (
            <Tooltip title={metadata?.name} placement={"top"}>
              <Typography
                variant={"body2"}
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {metadata?.name}
              </Typography>
            </Tooltip>
          );
        },
      },
      {
        accessorKey: "description",
        header: "Description",
        size: 250,
        accessorFn: ({ metadata }) => {
          return (
            <Tooltip title={metadata?.description} placement={"top"}>
              <Typography
                variant={"body2"}
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {metadata?.description}
              </Typography>
            </Tooltip>
          );
        },
      },
      {
        accessorKey: "intent",
        header: "Intent",
        size: 250,
        accessorFn: ({ intent }) => {
          return (
            <Tooltip title={intent?.summary} placement={"top"}>
              <Typography
                variant={"body2"}
                sx={{
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                }}
              >
                {intent?.summary}
              </Typography>
            </Tooltip>
          );
        },
      },
      {
        accessorKey: "agents",
        header: "Agents",
        accessorFn: ({ spec }) => {
          return (
            <Stack
              sx={{
                gap: "10px",
                flexDirection: "row",
                alignItems: "center",
                cursor: "pointer",
              }}
            >
              <Tags
                tags={
                  (spec?.agency?.agents ?? spec?.agents)
                    ?.map((agent) =>
                      typeof agent === "object" &&
                      agent !== null &&
                      "id" in agent
                        ? (agent.id ?? "")
                        : typeof agent === "object" &&
                            agent !== null &&
                            "metadata" in agent
                          ? String(
                              (agent as { metadata?: { name?: string } })
                                .metadata?.name ?? "",
                            )
                          : "",
                    )
                    .filter(
                      (id): id is string =>
                        typeof id === "string" && Boolean(id),
                    )
                    .map((id) => ({ name: id })) ?? []
                }
                minDisplayed={1}
              />
            </Stack>
          );
        },
      },
    ],
    [],
  );

  const tableRef = CreateTableInstance({
    data: data,
    columns,
    isLoading: isLoading,
    rowCount: data?.length,
    title: { label: title ?? "" },
    topToolbarProps: {
      export: { enableExport: false },
      // enableArrangeColumns: false,
      onReload: onReload,
    },
    // enableTopToolbar: false,
    enableRowActions: true,

    enableSorting: true,
    enableColumnResizing: true,
    renderEmptyRowsFallback: () => (
      <EmptyState
        title={"No MASes"}
        description={"Try adjusting your filters"}
      />
    ),

    state: { columnVisibility, sorting, rowSelection },
    onColumnVisibilityChange: setColumnVisibility,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    renderRowActionMenuItems: ({ row, closeMenu }) => [
      <MenuItem key="edit" onClick={() => onMasClick?.(row.original)}>
        <ListItemIcon>
          <EditIcon />
        </ListItemIcon>
        Edit
      </MenuItem>,
      <MenuItem
        key="duplicate"
        onClick={() => {
          handleOpenDuplicate(row.original);
          closeMenu();
        }}
      >
        <ListItemIcon>
          <DuplicateIcon />
        </ListItemIcon>
        Duplicate
      </MenuItem>,
      <MenuItem
        key="delete"
        onClick={() => {
          const name = row.original.metadata?.name;
          if (name) handleRequestDelete([name]);
          closeMenu();
        }}
      >
        <ListItemIcon>
          <DeleteIcon />
        </ListItemIcon>
        Delete
      </MenuItem>,
    ],

    renderToolbarInternalActions: ({ table }) => {
      const selectedRows = table.getSelectedRowModel().rows;
      const hasSelection = selectedRows.length > 0;
      return (
        <Button
          variant="primary"
          color="negative"
          disabled={!hasSelection}
          onClick={() => {
            const names = selectedRows
              .map((row) => row.original.metadata?.name)
              .filter((n): n is string => Boolean(n));
            if (names.length > 0) handleRequestDelete(names);
          }}
        >
          Delete Selected ({selectedRows.length})
        </Button>
      );
    },

    muiTableBodyRowProps: ({ row }) => {
      return {
        onClick: onMasClick ? () => onMasClick(row.original) : undefined,
        sx: {
          cursor: "pointer",
        },
      };
    },
  });

  return (
    <>
      {duplicateSuccess && (
        <Alert
          severity="success"
          onClose={() => setDuplicateSuccess("")}
          sx={{
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 1000,
            whiteSpace: "pre-wrap",
          }}
        >
          {duplicateSuccess}
        </Alert>
      )}
      <MaterialReactTable table={tableRef} />
      <Dialog open={deleteDialogOpen} onClose={handleCancelDelete}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {pendingDeleteNames.length === 1
              ? `Are you sure you want to delete "${pendingDeleteNames[0]}"?`
              : `Are you sure you want to delete the ${pendingDeleteNames.map((name) => `"${name}"`).join(", ")} applications?`}
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

      <Dialog
        open={duplicateDialogOpen}
        onClose={handleCloseDuplicate}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Duplicate Application</DialogTitle>
        <DialogContent>
          {duplicateError && (
            <Alert severity="error" sx={{ mb: 2, whiteSpace: "pre-wrap" }}>
              {duplicateError}
            </Alert>
          )}
          <TextField
            autoFocus
            margin="dense"
            label="MAS Name"
            placeholder="Enter a name for the new MAS"
            variant="standard"
            autoComplete="off"
            fullWidth
            value={duplicateName}
            onChange={(e) => {
              setDuplicateName(e.target.value);
              setDuplicateError("");
            }}
          />
          <TextField
            margin="dense"
            label="Description"
            placeholder="Enter a description"
            variant="standard"
            autoComplete="off"
            fullWidth
            multiline
            minRows={2}
            value={duplicateDescription}
            onChange={(e) => setDuplicateDescription(e.target.value)}
          />
          <TextField
            margin="dense"
            label="Intent"
            placeholder="Enter the intent summary"
            variant="standard"
            autoComplete="off"
            fullWidth
            multiline
            minRows={2}
            value={duplicateIntent}
            onChange={(e) => setDuplicateIntent(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDuplicate}>Cancel</Button>
          <Button
            variant="primary"
            onClick={handleConfirmDuplicate}
            disabled={isDuplicating}
          >
            {isDuplicating ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};
