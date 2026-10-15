//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle } from "@/components";
import {
  MaterialReactTable,
  type MRT_ColumnDef,
  type MRT_RowSelectionState,
  type MRT_SortingState,
} from "material-react-table";
import { CreateTableInstance, EmptyState, Button } from "@open-ui-kit/core";
import { Delete as DeleteIcon, Edit as EditIcon } from "@mui/icons-material";
import {
  Box,
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
import { useNavigate, useParams } from "react-router";
import { useCallback, useMemo, useState } from "react";
import {
  useOverlays,
  deleteOverlayApi,
  type OverlayEntry,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
type OverlayRow = Pick<OverlayEntry, "name" | "description" | "namespace">;

const Overlays = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { library = "" } = useParams();

  const { data: overlayEntries = [], isLoading } = useOverlays(library);
  const overlays = useMemo<OverlayRow[]>(
    () =>
      overlayEntries.map(({ name, description, namespace }) => ({
        name,
        description,
        namespace: namespace ?? "global",
      })),
    [overlayEntries],
  );

  const [sorting, setSorting] = useState<MRT_SortingState>([]);
  const [rowSelection, setRowSelection] = useState<MRT_RowSelectionState>({});
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [pendingDeleteNames, setPendingDeleteNames] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);

  const handleRequestDelete = useCallback((names: string[]) => {
    setPendingDeleteNames(names);
    setDeleteDialogOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await Promise.all(
        pendingDeleteNames.map((name) => deleteOverlayApi(library, name)),
      );
      setDeleteDialogOpen(false);
      setPendingDeleteNames([]);
      setRowSelection({});
      queryClient.resetQueries({ queryKey: ["overlays", library] });
    } finally {
      setDeleting(false);
    }
  }, [pendingDeleteNames, library, queryClient]);

  const handleCancelDelete = useCallback(() => {
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
  }, []);

  const columns = useMemo<MRT_ColumnDef<OverlayRow>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        Cell: ({ row }) => (
          <Typography
            sx={{
              cursor: "pointer",
              color: theme.palette.vars.interactivePrimaryDefaultDefault,
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {row.original.name}
          </Typography>
        ),
      },
      {
        accessorKey: "namespace",
        header: "Namespace",
        size: 180,
      },
      {
        accessorKey: "description",
        header: "Description",
      },
    ],
    [theme],
  );

  const tableRef = CreateTableInstance<OverlayRow>({
    data: overlays,
    columns,
    isLoading,
    rowCount: overlays.length,
    title: { label: "" },
    enableRowActions: true,
    enableSorting: true,
    enableColumnResizing: true,
    enableRowSelection: true,
    state: { sorting, rowSelection },
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    renderEmptyRowsFallback: () => (
      <EmptyState
        title="No Overlays"
        description="Create an overlay to get started"
      />
    ),
    renderRowActionMenuItems: ({ row, closeMenu }) => [
      <MenuItem
        key="edit"
        onClick={() => {
          navigate(
            `/${library}/overlays/${encodeURIComponent(row.original.name)}`,
          );
          closeMenu();
        }}
      >
        <ListItemIcon>
          <EditIcon />
        </ListItemIcon>
        Edit
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
            const names = selectedRows.map((r) => r.original.name);
            if (names.length > 0) handleRequestDelete(names);
          }}
        >
          Delete Selected ({selectedRows.length})
        </Button>
      );
    },
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () =>
        navigate(
          `/${library}/overlays/${encodeURIComponent(row.original.name)}`,
        ),
      sx: {
        cursor: "pointer",
      },
    }),
  });

  return (
    <Box sx={{ position: "relative" }}>
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
              Overlays
            </Typography>
            <Button
              variant="gradient"
              onClick={() => navigate(`/${library}/overlays/new`)}
            >
              New Overlay
            </Button>
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
              : `Are you sure you want to delete ${pendingDeleteNames.length} overlays?`}
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
    </Box>
  );
};

export default Overlays;
