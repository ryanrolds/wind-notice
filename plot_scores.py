"""Plot scoring curves for each factor."""
import numpy as np
import matplotlib.pyplot as plt
import sys
sys.path.insert(0, '.')
from wind_notice import score_wind, score_gusts, score_precipitation, score_cloud_cover, score_temperature, score_direction

fig, axes = plt.subplots(2, 3, figsize=(14, 8))
fig.suptitle('Scoring Functions by Factor', fontsize=16, fontweight='bold')

# Wind (avg mph)
ax = axes[0, 0]
x = np.linspace(0, 25, 500)
y = [score_wind([v]) for v in x]
ax.plot(x, y, 'b-', linewidth=2)
ax.set_title('Wind (35%)')
ax.set_xlabel('Avg Wind Speed (mph)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
ax.axvspan(10, 15, alpha=0.1, color='green', label='Ideal')
ax.legend()
ax.grid(True, alpha=0.3)

# Gusts (max mph)
ax = axes[0, 1]
x = np.linspace(0, 35, 500)
y = [score_gusts([10], [v]) for v in x]
ax.plot(x, y, 'r-', linewidth=2)
ax.set_title('Gusts (20%)')
ax.set_xlabel('Max Gust (mph)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
ax.axvspan(0, 20, alpha=0.1, color='green', label='Ideal')
ax.legend()
ax.grid(True, alpha=0.3)

# Precipitation (total inches)
ax = axes[0, 2]
x = np.linspace(0, 0.5, 500)
y = [score_precipitation([v]) for v in x]
ax.plot(x, y, 'c-', linewidth=2)
ax.set_title('Precipitation (15%)')
ax.set_xlabel('Total Precip (in)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
ax.grid(True, alpha=0.3)

# Cloud Cover (avg %)
ax = axes[1, 0]
x = np.linspace(0, 100, 500)
y = [score_cloud_cover([v]) for v in x]
ax.plot(x, y, 'gray', linewidth=2)
ax.set_title('Cloud Cover (5%)')
ax.set_xlabel('Avg Cloud Cover (%)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
ax.axvspan(30, 70, alpha=0.1, color='green', label='Ideal')
ax.legend()
ax.grid(True, alpha=0.3)

# Temperature (avg F)
ax = axes[1, 1]
x = np.linspace(50, 115, 500)
y = [score_temperature([v]) for v in x]
ax.plot(x, y, color='orange', linewidth=2)
ax.set_title('Temperature (15%)')
ax.set_xlabel('Avg Temp (°F)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
ax.axvspan(75, 95, alpha=0.1, color='green', label='Ideal')
ax.legend()
ax.grid(True, alpha=0.3)

# Direction (degrees)
ax = axes[1, 2]
x = np.linspace(0, 360, 500)
y = [score_direction([v]) for v in x]
ax.plot(x, y, 'purple', linewidth=2)
ax.set_title('Direction (10%)')
ax.set_xlabel('Wind Direction (°)')
ax.set_ylabel('Score')
ax.set_ylim(-0.05, 1.05)
# Add compass labels
ax.set_xticks([0, 45, 90, 135, 180, 225, 270, 315, 360])
ax.set_xticklabels(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N'], fontsize=8)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('scoring_curves.png', dpi=150, bbox_inches='tight')
print('Saved scoring_curves.png')
