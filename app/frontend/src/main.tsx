import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import App from "./App"
import { AppDialogProvider } from "./components/ui/app-dialog"
import "./index.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppDialogProvider>
      <App />
    </AppDialogProvider>
  </StrictMode>,
)
