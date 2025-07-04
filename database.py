import os
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.sql import func # for server_default=func.now()

# Define the database file path relative to the app's instance folder if using Flask instance folders,
# or just in the project root. For simplicity here, let's put it in the project root.
# A more robust Flask app might use app.instance_path.
DATABASE_URL = "sqlite:///./analysis_cache.db"

Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}) # check_same_thread for SQLite
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class AnalyzedFile(Base):
    __tablename__ = "analyzed_files"

    id = Column(Integer, primary_key=True, index=True)
    repo_url = Column(String, nullable=False)
    commit_sha = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_content = Column(Text) # Can be large
    initial_analysis_text = Column(Text)
    analysis_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Unique constraint to prevent duplicate entries for the same file version
    __table_args__ = (UniqueConstraint('repo_url', 'commit_sha', 'file_path', name='_repo_commit_file_uc'),)

    # Relationship to a batch if we create a Batch table
    # batch_id = Column(Integer, ForeignKey('analysis_batches.id'))
    # batch = relationship("AnalysisBatch", back_populates="analyzed_files")


class FeatureSynthesis(Base):
    __tablename__ = "feature_syntheses" # Corrected table name pluralization

    id = Column(Integer, primary_key=True, index=True)
    # How to link this to a set of files?
    # Option 1: Link to a commit_sha if all files from a single commit are processed together for synthesis
    commit_sha = Column(String, index=True) # Assuming synthesis is per commit for now
    # Option 2: Create a Batch table and link to batch_id
    # batch_id = Column(Integer, ForeignKey('analysis_batches.id'), unique=True) # A batch has one synthesis

    # Store the list of file IDs that contributed to this synthesis, if needed for detailed tracking.
    # This could be a comma-separated string of AnalyzedFile.id or a proper association table.
    # For simplicity, let's assume for now the synthesis applies to a "set" identified externally (e.g. by commit + user selection at runtime)
    # and we mainly store the result.

    synthesis_text = Column(Text, nullable=False)
    synthesis_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # If using a Batch table:
    # batch = relationship("AnalysisBatch", back_populates="synthesis")


# Example of a Batch table if we want to group analyses more formally:
# class AnalysisBatch(Base):
#     __tablename__ = "analysis_batches"
#     id = Column(Integer, primary_key=True, index=True)
#     created_at = Column(DateTime(timezone=True), server_default=func.now())
#     repo_url = Column(String)
#     commit_sha = Column(String) # Or other criteria defining the batch
#     analyzed_files = relationship("AnalyzedFile", back_populates="batch")
#     synthesis = relationship("FeatureSynthesis", back_populates="batch", uselist=False) # One-to-one with batch


def init_db():
    # Create all tables in the database.
    # This is usually called once at application startup.
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")

if __name__ == "__main__":
    # This allows creating the DB schema by running `python database.py`
    init_db()
