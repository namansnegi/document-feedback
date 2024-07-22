document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM fully loaded and parsed'); // Debug statement to ensure the JS is running
    const fileInput = document.getElementById('fileInput');
    const uploadButton = document.getElementById('upload-button');
    
    if (uploadButton && fileInput) {
        uploadButton.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', () => {
            const file = fileInput.files[0];
            if (file) {
                console.log('File selected:', file.name); 
                handleFileUpload(file);
            } else {
                console.log('No file selected'); 
            }
        });
    } else {
        console.log('Upload button or file input element not found'); // Debug statement if the elements are not found
    }
});

async function handleFileUpload(file) {
    if (file) {
        const reader = new FileReader();
        reader.onload = async function(e) {
            const fileContent = e.target.result.split(',')[1]; // Base64 content
            const fileName = file.name;

            try {
                const response = await fetch('/process-document', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ fileContent, fileName }),
                });

                const data = await response.json();
                if (data.error) {
                    console.error('Error:', data.error);
                    alert('An error occurred during processing: ' + data.error);
                } else {
                    const ocrText = extractTextFromBlocks(data.Blocks);
                    const correctedResponse = await fetch('/correct-text', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ text: ocrText }),
                    });

                    const correctedData = await correctedResponse.json();
                    // Store the corrected text in sessionStorage for retrieval on the next page
                    sessionStorage.setItem('correctedText', correctedData.corrected_text);

                    // Redirect to the results page
                    window.location.href = '/process';
                }
            } catch (error) {
                console.error('Error:', error);
                alert('An error occurred during processing.');
            }
        };
        reader.readAsDataURL(file);
    }
}


function extractTextFromBlocks(blocks) {
    // Implementation for extracting text from blocks
    return blocks.map(block => block.Text).join(' ');
}
