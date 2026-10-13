//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import ShikiHighlighter from "react-shiki";
import { Box, useTheme } from "@mui/material";

interface CodeBlockProps {
  code: string;
  language?: string;
  preStyle?: React.CSSProperties;
}

const CodeBlock = ({
  code,
  language = "yaml",
  preStyle = {},
}: CodeBlockProps) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Box
      sx={{
        borderRadius: "8px",
        overflow: "auto",
        fontSize: "13px",
        lineHeight: 1.6,
        "& pre": {
          margin: 0,
          padding: "16px",
          ...preStyle,
        },
      }}
    >
      <ShikiHighlighter
        language={language}
        theme={isDark ? "github-dark" : "github-light"}
      >
        {code}
      </ShikiHighlighter>
    </Box>
  );
};

export default CodeBlock;
export { CodeBlock };
