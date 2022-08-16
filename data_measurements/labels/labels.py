import utils.dataset_utils as utils
from os.path import join as pjoin
import evaluate

LABEL_FIELD = "labels"
LABEL_NAMES = "label_names"
LABEL_LIST = "label_list"
LABEL_JSON = "labels.json"
LABEL_FIG_JSON = "labels_fig.json"
LABEL_MEASUREMENT = "label_measurement"
# Specific to the evaluate library
EVAL_LABEL_MEASURE = "label_distribution"
EVAL_LABEL_ID = "labels"
EVAL_LABEL_FRAC = "fractions"


def extract_label_names(label_field, ds_name, config_name):
    ds_name_to_dict = dataset_utils.get_dataset_info_dicts(ds_name)
    label_names = map_labels(label_field, ds_name_to_dict, ds_name, config_name)
    return label_names


class Labels:
    """
    Uses the Dataset to extract the label column and compute label measurements.
    """

    def __init__(self, dset, ds_name=None, config_name=None, label_field=None, label_names=None,
                 cache_path=None, use_cache=False, save=False):
        # Input HuggingFace Dataset.
        self.dset = dset
        # These are used to extract label names, when the label names
        # are stored in the Dataset object but not in the "label" column
        # we are working with, which may instead just be ints corresponding to
        # the names
        self.ds_name = ds_name
        self.config_name = config_name
        if not label_field:
            self.label_field = LABEL_FIELD
            print(
                "Name of the label field not provided; assuming %s " %
                LABEL_FIELD)
        # The set of label names (if known)
        self.label_names = label_names
        self.use_cache = use_cache
        self.cache_path = pjoin(cache_path, LABEL_FIELD)
        # For measurement cache and metadata in the json file
        self.label_results_dict = {}
        # Filename for the figure
        self.labels_fig_json_fid = pjoin(self.cache_path, LABEL_FIG_JSON)
        # Filename for the measurement cache
        self.labels_json_fid = pjoin(self.cache_path, LABEL_JSON)
        # Values in the Dataset label column
        self.label_list = []
        # Distributional information -- what we actually report
        self.label_measurement = {}
        # Label figure
        self.fig_labels = None
        # Whether to save results
        self.save = save

    def load_or_prepare_labels(self):
        """
        For the DMT, we only need the figure.
        This checks whether the figure exists, first.
        If it doesn't, it creates one.
        """
        # Bools to track whether the data is newly prepared,
        # in which case we may want to cache it.
        prepared_fig = False
        prepared_measurement = False
        if self.use_cache:
            # Figure exists. It's all we need for the UI.
            if exists(self.labels_fig_json_fid):
                self.fig_labels = utils.read_plotly(self.labels_fig_json_fid)
            # Measurements exist, just not the figure; make it
            elif exists(self.label_json_fid):
                # Loads the label list, names, and results
                self.load_labels()
                # Makes figure from the label list, names, results
                self.fig_labels = make_label_fig(self.label_list, self.label_names, self.label_measurement)
                # We have newly prepared this figure, huzzah!
                prepared_fig = True
        # If we have not gotten the figure, calculate afresh.
        # This happens when the cache is not used.
        if not prepared_fig:
            self.prepare_labels()
            # We've successfully calculated the distribution.
            # This is empty when there is not a label field we can find in the data.
            if self.label_measurement:
                self.fig_labels = make_label_fig(self.label_list, self.label_names,
                                                 self.label_measurement)
                prepared_measurement = True
                prepared_fig = True
        if self.save:
            # TODO: Sould this be called from a utils file instead?
            # Create the cache path if it's not there.
            os.makedirs(self.cache_path, exist_ok=True)
            # If the measurement is newly cßalculated, save it.
            if prepared_measurement:
                self.label_results_dict = make_label_results_dict(self.label_measurement, self.label_list, self.label_names)
                utils.write_json(self.label_results_dict, self.labels_json_fid)
            # If the figure is newly created, save it
            if prepared_fig:
                utils.write_plotly(self.fig_labels, self.labels_fig_json_fid)

    def prepare_labels(self):
        """ Uses the evaluate library to return the label distribution. """
        # The input Dataset object
        if self.label_field in self.dset.keys():
            self.label_list = self.dset[self.label_field]
            # Have to extract the label names from the Dataset object when the
            # actual dataset columns are just ints representing the label names.
            self.label_names = self.extract_label_names(self.label_field, self.ds_name, self.config_name)
            # Get the evaluate library's measurement for label distro.
            label_distribution = evaluate.load(EVAL_LABEL_MEASURE)
            # Measure the label distro.
            self.label_measurement = label_distribution.compute(data=self.label_list)
        else:
            print("Could not find label field %s " % self.label_field)

    def load_labels(self):
        self.label_results_dict = utils.read_json(self.labels_json_fid)
        self.label_list = self.label_results_dict[LABEL_LIST]
        self.label_names = self.label_results_dict[LABEL_NAMES]
        self.label_measurement = self.label_results_dict[LABEL_MEASUREMENT]


def map_labels(label_field, ds_name_to_dict, ds_name, config_name):
    label_field, label_names = (
        ds_name_to_dict[ds_name][config_name]["features"][label_field][0]
        if len(ds_name_to_dict[ds_name][config_name]["features"][label_field]) > 0
        else ((), [])
    )
    return label_names

def make_label_results_dict(label_measurement, label_list, label_names):
    label_dict = {LABEL_MEASUREMENT: copy(label_measurement),
                  LABEL_LIST: label_list, LABEL_NAMES: label_names}
    return label_dict

def make_label_fig(label_list, label_names, results, chart_type="pie"):
    if chart_type == "bar":
        fig_labels = plt.bar(results[EVAL_LABEL_MEASURE][EVAL_LABEL_ID],
                             results[EVAL_LABEL_MEASURE][EVAL_LABEL_FRAC])
    else:
        if chart_type != "pie":
            print("Oops! Don't have that chart-type implemented.")
            print("Making the default pie chart")
        fig_labels = px.pie(label_list,
                            values=results[EVAL_LABEL_MEASURE][EVAL_LABEL_ID],
                            names=label_names)
        #     labels = label_df[label_field].unique()
        #     label_sums = [len(label_df[label_df[label_field] == label]) for label in labels]
        #     fig_labels = px.pie(label_df, values=label_sums, names=label_names)
        #     return fig_labels
    return fig_labels