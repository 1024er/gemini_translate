from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
import os
import threading
import pandas as pd
from openai import OpenAI
import json
import random

# Initialize the Flask app
app = Flask(__name__, static_folder="frontend", template_folder="frontend")
CORS(app)

# OpenAI Gemini API client
client = OpenAI(
    api_key="AIzaSyAspULJ-uHsAyrLs8QLybQcCXSepju5PBo",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

# Task storage
tasks = {}

# Function to perform translation
def translate_file(task_id, file_path, target_language):
    try:
        # Read the CSV file
        df = pd.read_csv(file_path)

        # Identify columns to translate
        columns_to_translate = ["Title", "Body (HTML)"]
        columns_to_translate += [col for col in df.columns if col.startswith("Option") and ("Name" in col or "Value" in col)]

        # Group rows by Handle
        grouped = df.groupby("Handle")

        total_rows = len(df)
        processed_rows = 0

        for handle, group in grouped:
            # Filter out rows where all translate columns are empty
            valid_rows = group[group[columns_to_translate].notnull().any(axis=1)]
            if valid_rows.empty:
                # Skip this group if all rows are empty in columns_to_translate
                # For rows that are not valid, retain their original content
                for idx, row in group.iterrows():
                    for col in columns_to_translate:
                        if col in df.columns and pd.isna(row[col]):
                            df.at[idx, col] = row[col]
                    # Ensure the original order is maintained
                    df.at[idx, :] = row
                
                processed_rows += len(group)
                tasks[task_id]['progress'] = int((processed_rows / total_rows) * 100)
                continue

            # Create a JSON list with the fields to translate for each valid row in the group
            json_list = []
            for _, row in valid_rows.iterrows():
                translate_data = {col: row[col] for col in columns_to_translate if col in df.columns and pd.notna(row[col])}
                json_list.append(translate_data)

            # Convert the JSON list to a string
            json_string = json.dumps(json_list)

            # Define key-pool
            key_pool = ["AIzaSyAspULJ-uHsAyrLs8QLybQcCXSepju5PBo"]

            # Retry logic for translation
            retries = 0
            max_retries = 20
            failed = True
            while retries < max_retries:
                try:
                    api_key = random.choice(key_pool)
                    client = OpenAI(
                        api_key=api_key,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                    )
            
                    response = client.chat.completions.create(
                        model="gemini-1.5-flash",
                        n=1,
                        messages=[
                            {"role": "system", "content": f"You are a helpful assistant that translates text to {target_language}.You will be provided with a json format input, please translate the value corresponding to each json field, the return format is still json, the key remains unchanged, and the value is replaced with the translated content."},
                            {"role": "user", "content": json_string}
                        ]
                    )
            
                    response = response.choices[0].message.content.strip().strip('```json').strip('```')
                    translated_json_list = json.loads(response)
                    failed = False
                    break
                except Exception as e:
                    print(f"Error during translation for group with Handle {handle}: {e}")
                    retries += 1
            
                    if retries >= max_retries:
                        print(f"Max retries reached for Handle {handle}. Marking as failed.")
                        # Mark the Handle as failed
                        for idx in group.index:
                            df.at[idx, 'Handle'] = f"{df.at[idx, 'Handle']}-failed"
                        # Retain original content
                        for idx, row in group.iterrows():
                            for col in columns_to_translate:
                                if col in df.columns and pd.isna(row[col]):
                                    df.at[idx, col] = row[col]
            
                        # Update progress after failure
                        processed_rows += len(valid_rows)
                        tasks[task_id]['progress'] = int((processed_rows / total_rows) * 100)
                        break
            if not failed:
                # Update progress after each API call, including successful translation
                processed_rows += len(valid_rows)
                tasks[task_id]['progress'] = int((processed_rows / total_rows) * 100)
    
                # Update the DataFrame with the translated values
                for idx, translated_json in zip(valid_rows.index, translated_json_list):
                    for col, translated_value in translated_json.items():
                        if col in df.columns:
                            df.at[idx, col] = translated_value
    
                # Mark all rows in the group as processed
                processed_rows += len(group)
                tasks[task_id]['progress'] = int((processed_rows / total_rows) * 100)

        # Save translated DataFrame to a new file
        output_file = file_path.replace('.csv', f'_translated_{target_language}.csv')
        df.to_csv(output_file, index=False)

        tasks[task_id]['status'] = 'completed'
        tasks[task_id]['output_file'] = output_file
    except Exception as e:
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['message'] = str(e)
        print(f"Error occurred during translation task {task_id}: {e}")

# Route to serve the frontend
@app.route('/')
def index():
    return render_template('index.html')

# Route to handle file upload and start translation
@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        file = request.files['file']
        target_language = request.form['language']

        # Save the file
        file_path = os.path.join('uploads', file.filename)
        os.makedirs('uploads', exist_ok=True)
        file.save(file_path)

        # Create a new task
        task_id = str(len(tasks) + 1)
        tasks[task_id] = {
            'status': 'in_progress',
            'progress': 0
        }

        # Start a new thread for translation
        thread = threading.Thread(target=translate_file, args=(task_id, file_path, target_language))
        thread.start()

        return jsonify({'task_id': task_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Route to check task status
@app.route('/status/<task_id>', methods=['GET'])
def check_status(task_id):
    if task_id in tasks:
        return jsonify(tasks[task_id])
    else:
        return jsonify({'error': 'Task not found'}), 404

# Route to download translated file
@app.route('/download/<task_id>', methods=['GET'])
def download_file(task_id):
    if task_id in tasks and tasks[task_id]['status'] == 'completed':
        output_file = tasks[task_id]['output_file']
        directory = os.path.dirname(output_file)
        filename = os.path.basename(output_file)
        return send_from_directory(directory, filename, as_attachment=True)
    else:
        return jsonify({'error': 'File not ready or task not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5005)
