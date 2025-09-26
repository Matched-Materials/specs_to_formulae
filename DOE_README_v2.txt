 
DOE v2 quick start
------------------
This generator broadens the search to include biofiber, biochar, and optional INTUNE compatibilizer.
It also allows categorical elastomer choice (PBE, POE, OBC) via split runs.

Examples:
  python formulation_doe_generator_v2.py -n 60 --include-biofiber --use-intune -o doe_biofiber_intune.csv
  python formulation_doe_generator_v2.py -n 60 --include-biochar -o doe_biochar.csv
  python formulation_doe_generator_v2.py -n 80 --elastomer_family POE --include-biofiber --include-biochar -o doe_POE_bio.csv

The output includes estimated cost, CO2e, and biogenic mass fraction based on ingredient_library.json metadata.
