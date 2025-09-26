import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt
import seaborn as sns
import os

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))

DOE_CSV_PATH = os.path.join(PROJECT_ROOT, "results", "datasets", "initial_doe.csv")
OUTPUT_CSV_PATH = os.path.join(PROJECT_ROOT, "results", "datasets", "doe_pca_transformed.csv")
OUTPUT_PLOT_PATH = os.path.join(PROJECT_ROOT, "results", "plots", "pca_explained_variance.png")

# --- 1. Load the DOE data ---
def load_data(path):
    """Loads the DOE csv file."""
    try:
        return pd.read_csv(path)
    except FileNotFoundError:
        print(f"Error: DOE file not found at {path}")
        print("Please run formulation_doe_generator.py first.")
        exit()

# --- 2. Prepare data for PCA ---
def get_features_for_pca(df):
    """Selects and returns the numeric feature columns for PCA."""
    # These are the 'knobs' we turned in the DOE
    feature_cols = [
        'rPP_ratio_in_base',
        'Elastomer_wt_pct',
        'Talc_wt_pct',
        'Compatibilizer_wt_pct',
        'Nucleator_ppm',
        'Compounding_Melt_Temp_C',
        'Compounding_Screw_Speed_rpm',
        'Molding_Melt_Temp_C',
        'Molding_Mold_Temp_C',
        'Molding_Pack_Pressure_MPa',
    ]
    return df[feature_cols]

# --- 3. Perform PCA ---
def perform_pca(features_df):
    """Scales data and performs PCA."""
    # Standardize the features (mean=0, variance=1) is crucial for PCA
    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(features_df)
    
    # Fit PCA
    pca = PCA()
    principal_components = pca.fit_transform(scaled_features)
    
    # Create a DataFrame with the principal components
    pc_df = pd.DataFrame(
        data=principal_components, 
        columns=[f'PC_{i+1}' for i in range(features_df.shape[1])]
    )
    
    return pca, pc_df, features_df.columns

# --- 4. Analyze and Visualize PCA Results ---
def analyze_pca(pca, feature_names):
    """Prints and plots PCA results."""
    explained_variance = pca.explained_variance_ratio_
    cumulative_variance = np.cumsum(explained_variance)
    
    print("--- PCA Analysis ---")
    print(f"Explained variance by component: {np.round(explained_variance, 3)}")
    print(f"Cumulative variance: {np.round(cumulative_variance, 3)}")
    
    # Find how many components are needed to explain >95% of variance
    n_components_95 = np.argmax(cumulative_variance >= 0.95) + 1
    print(f"\nNote: {n_components_95} components explain >95% of the variance.")

    # Print the component loadings
    print("\n--- Principal Component Loadings ---")
    loadings = pd.DataFrame(pca.components_.T, columns=[f'PC_{i+1}' for i in range(len(feature_names))], index=feature_names)
    print("The table below shows how original variables contribute to each Principal Component.")
    print(loadings.round(3))
    
    # Plotting
    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(range(1, len(explained_variance) + 1), explained_variance, alpha=0.7, align='center', label='Individual explained variance')
    ax.step(range(1, len(cumulative_variance) + 1), cumulative_variance, where='mid', label='Cumulative explained variance', color='red')
    ax.set_ylabel('Explained variance ratio')
    ax.set_xlabel('Principal component index')
    ax.set_title('PCA Explained Variance')
    ax.set_xticks(range(1, len(explained_variance) + 1))
    ax.legend(loc='best')
    plt.tight_layout()
    
    print(f"\nSaving explained variance plot to '{OUTPUT_PLOT_PATH}'...")
    plt.savefig(OUTPUT_PLOT_PATH)
    plt.close()

# --- Main Execution ---
def main():
    """Main function to run the dimensionality reduction."""
    print("Loading the generated DOE data...")
    doe_df = load_data(DOE_CSV_PATH)
    features = get_features_for_pca(doe_df)
    
    print("\nPerforming Principal Component Analysis (PCA)...")
    pca_model, pc_df, feature_names = perform_pca(features)
    analyze_pca(pca_model, feature_names)
    
    final_df = pd.concat([doe_df[['DOE_ID', 'Cost_USD_per_kg', 'Carbon_kgCO2e_per_kg']], pc_df], axis=1)
    print(f"\nSaving PCA-transformed data to '{OUTPUT_CSV_PATH}'...")
    final_df.to_csv(OUTPUT_CSV_PATH, index=False)
    print("\nDimensionality Reduction Complete.")

if __name__ == "__main__":
    main()