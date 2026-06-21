module.exports = {
  apps: [
    {
      name: "model-checker",
      script: "uvicorn",
      args: "app:app --host 0.0.0.0 --port 8000 --reload",
      interpreter: "python3",
      cwd: __dirname,
      watch: ["app.py", "index.html"],
      watch_delay: 1000,
      ignore_watch: ["__pycache__", ".git", "node_modules"],
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      error_file: "logs/err.log",
      out_file: "logs/out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: true,
    },
  ],
};
