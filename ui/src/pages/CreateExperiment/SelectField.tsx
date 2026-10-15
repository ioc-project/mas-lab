//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import { Stack, Typography, type SxProps, type Theme } from "@mui/material";
import { Select, type SelectProps } from "@open-ui-kit/core";

export interface SelectFieldProps<T = string> extends Omit<
  SelectProps<T>,
  "label" | "variant" | "sx"
> {
  /** Label rendered above the select (the kit Select has no built-in label). */
  label: string;
  /** Text shown while no value is selected (single select only). */
  placeholder?: string;
  /** Width of the whole field (label + select). Defaults to the kit width. */
  width?: number | string;
  /** Style overrides for the outer wrapper, e.g. flex sizing. */
  containerSx?: SxProps<Theme>;
  children: ReactNode;
}

const toSxArray = (sx: SxProps<Theme> | undefined) =>
  Array.isArray(sx) ? sx : sx ? [sx] : [];

/**
 * Labelled wrapper around the kit `Select`: `subtitle2` label above a `displayEmpty` select.
 */
export function SelectField<T = string>({
  label,
  required,
  placeholder,
  width,
  fullWidth,
  containerSx,
  renderValue,
  children,
  ...props
}: SelectFieldProps<T>) {
  const resolvedWidth = fullWidth ? "100%" : width;

  return (
    <Stack
      spacing={0.5}
      sx={[
        {
          minWidth: 0,
          ...(resolvedWidth !== undefined && { width: resolvedWidth }),
        },
        ...toSxArray(containerSx),
      ]}
    >
      <Typography
        variant="subtitle2"
        sx={(theme) => ({ color: theme.palette.vars.baseTextDefault })}
      >
        {label}
        {required ? " *" : ""}
      </Typography>
      <Select<T>
        displayEmpty
        renderValue={
          renderValue ??
          ((selected) => (selected ? String(selected) : (placeholder ?? "")))
        }
        {...props}
        sx={{ width: "100%" }}
      >
        {children}
      </Select>
    </Stack>
  );
}
