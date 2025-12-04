"""Progress tracking with checkpoint system for resumable processing."""

import json
from pathlib import Path
from typing import Set, Optional
from datetime import datetime


class ProgressTracker:
    """Tracks processing progress with checkpoint system."""
    
    def __init__(self, checkpoint_file: str):
        """
        Initialize progress tracker.
        
        Args:
            checkpoint_file: Path to checkpoint JSON file
        """
        self.checkpoint_file = Path(checkpoint_file)
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self.processed_indices: Set[int] = set()
        self.last_save_time: Optional[str] = None
        
        # Load existing checkpoint if available
        self.load_checkpoint()
    
    def load_checkpoint(self):
        """Load checkpoint from file."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_indices = set(data.get('processed_indices', []))
                    self.last_save_time = data.get('last_save_time')
                    print(f"Loaded checkpoint: {len(self.processed_indices)} businesses already processed")
            except Exception as e:
                print(f"Warning: Could not load checkpoint: {e}")
                self.processed_indices = set()
    
    def save_checkpoint(self):
        """Save checkpoint to file."""
        try:
            data = {
                'processed_indices': sorted(list(self.processed_indices)),
                'last_save_time': datetime.now().isoformat()
            }
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.last_save_time = data['last_save_time']
        except Exception as e:
            print(f"Warning: Could not save checkpoint: {e}")
    
    def is_processed(self, index: int) -> bool:
        """
        Check if business at index has been processed.
        
        Args:
            index: Business index
            
        Returns:
            True if already processed
        """
        return index in self.processed_indices
    
    def mark_processed(self, index: int):
        """
        Mark business at index as processed.
        
        Args:
            index: Business index
        """
        self.processed_indices.add(index)
    
    def get_progress_stats(self) -> dict:
        """
        Get progress statistics.
        
        Returns:
            Dictionary with progress stats
        """
        return {
            'processed_count': len(self.processed_indices),
            'last_save_time': self.last_save_time
        }

