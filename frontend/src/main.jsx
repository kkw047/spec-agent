import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

// React 앱 시작점
// index.html의 #root에 SPEC Agent 화면(App.jsx)을 붙입니다.
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
