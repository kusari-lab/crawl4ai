"""CSV reading and writing with progress tracking."""

import csv
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class CSVHandler:
    """Handles CSV reading and writing operations."""
    
    def __init__(self, input_file: str, output_file: str):
        """
        Initialize CSV handler.
        
        Args:
            input_file: Path to input CSV file
            output_file: Path to output CSV file
        """
        self.input_file = Path(input_file)
        self.output_file = Path(output_file)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
    
    def read_businesses(self) -> List[Dict]:
        """
        Read businesses from input CSV.
        
        Returns:
            List of business dictionaries
        """
        businesses = []
        
        try:
            with open(self.input_file, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Clean up empty values
                    cleaned_row = {k: v.strip() if v else '' for k, v in row.items()}
                    businesses.append(cleaned_row)
        except Exception as e:
            raise Exception(f"Error reading CSV file: {e}")
        
        return businesses
    
    def write_enhanced_csv(self, businesses: List[Dict], fieldnames: Optional[List[str]] = None):
        """
        Write enhanced CSV with phone numbers.
        
        Args:
            businesses: List of business dictionaries with phone data
            fieldnames: Optional list of field names (auto-detected if None)
        """
        if not businesses:
            return
        
        # Determine fieldnames
        if fieldnames is None:
            # Get all keys from first business
            fieldnames = list(businesses[0].keys())
        
        # Ensure output columns exist
        required_columns = ['phone_number', 'source', 'confidence_score', 'extraction_date']
        for col in required_columns:
            if col not in fieldnames:
                fieldnames.append(col)
        
        try:
            with open(self.output_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for business in businesses:
                    # Ensure all required columns exist
                    for col in required_columns:
                        if col not in business:
                            business[col] = ''
                    
                    writer.writerow(business)
        except Exception as e:
            raise Exception(f"Error writing CSV file: {e}")
    
    def add_phone_data(self, business: Dict, phone: Optional[str], 
                       source: Optional[str], confidence: Optional[str]) -> Dict:
        """
        Add phone data to business dictionary.
        
        Args:
            business: Business dictionary
            phone: Phone number
            source: Source name
            confidence: Confidence score
            
        Returns:
            Updated business dictionary
        """
        business['phone_number'] = phone or ''
        business['source'] = source or ''
        business['confidence_score'] = confidence or ''
        business['extraction_date'] = datetime.now().isoformat()
        return business

