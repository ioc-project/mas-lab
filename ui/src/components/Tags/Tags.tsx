//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  Box,
  GeneralSize,
  Stack,
  Tag,
  TagBackgroundColorVariants,
  Tooltip,
  Typography,
  useTheme,
} from "@open-ui-kit/core";
import { useMemo, isValidElement } from "react";

interface Tag {
  name: string;
  icon?: React.ReactNode;
  color?: TagBackgroundColorVariants;
}

interface TagsProps {
  tags: Tag[];
  minDisplayed?: number;
}

export const Tags = ({ tags, minDisplayed = 1 }: TagsProps) => {
  const theme = useTheme();
  const displayedTags = useMemo(() => {
    return (tags ?? []).slice(0, minDisplayed);
  }, [tags, minDisplayed]);

  const overflowTags = useMemo(() => {
    return (tags ?? []).slice(minDisplayed);
  }, [tags, minDisplayed]);

  const OverflowTooltipContent = (
    <Stack direction="column" sx={{ gap: "4px" }}>
      {overflowTags.map((tag) => (
        <Stack
          sx={{ direction: "row", gap: "4px", alignItems: "center" }}
          key={tag.name}
        >
          <Typography variant="caption">{tag.name}</Typography>
        </Stack>
      ))}
    </Stack>
  );

  const defaultColor =
    tags?.[0]?.color ?? TagBackgroundColorVariants.AccentGWeak;

  return (
    <Stack
      sx={{
        gap: "10px",
        flexDirection: "row",
        alignItems: "center",
        cursor: "pointer",
      }}
    >
      {displayedTags.map((tag) => (
        <Tag
          key={tag.name}
          sx={{ backgroundColor: theme.palette.vars.controlBackgroundMedium }}
          size={GeneralSize.Medium}
          icon={isValidElement(tag?.icon) ? tag.icon : undefined}
        >
          {tag.name}
        </Tag>
      ))}
      {overflowTags.length > 0 && (
        <Tooltip title={OverflowTooltipContent} placement={"top"}>
          <Box>
            <Tag
              color={defaultColor}
              size={GeneralSize.Medium}
              sx={{
                backgroundColor: theme.palette.vars.controlBackgroundMedium,
              }}
            >
              +{overflowTags.length}
            </Tag>
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
};
