# Stratpoint RAG Chatbot UI

This is the Streamlit frontend for the Stratpoint RAG Chatbot capstone project.

## How to Run

1. Open your terminal and navigate to this directory:
   ```bash
   cd src/stratpoint_rag/ui/
   ```
2. Start the Streamlit server:
   ```bash
   streamlit run app.py
   ```

## Configuration

By default, the UI expects the FastAPI backend to be running at `http://localhost:8000`. 
If your API is hosted elsewhere, you can point the UI to it by setting the `STRATPOINT_API_URL` environment variable:

```bash
# On Windows (PowerShell)
$env:STRATPOINT_API_URL="http://your-api-url.com"
streamlit run app.py

# On Linux/macOS
STRATPOINT_API_URL=http://your-api-url.com streamlit run app.py
```
