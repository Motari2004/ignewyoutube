# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Install system dependencies (ffmpeg is crucial for yt-dlp)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create downloads directory
RUN mkdir -p downloads

# Expose the port Render will use
EXPOSE 10000

# Run the app using Hypercorn with Quart
CMD ["sh", "-c", "hypercorn app:app --bind 0.0.0.0:${PORT:-10000}"]