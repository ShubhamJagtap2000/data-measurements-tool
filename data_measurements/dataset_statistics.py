# Copyright 2021 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import nltk
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import seaborn as sns
import statistics
import utils
import utils.dataset_utils as ds_utils
from data_measurements.embeddings.embeddings import Embeddings
from data_measurements.labels import labels
from data_measurements.text_duplicates import text_duplicates as td
from data_measurements.npmi import npmi
# TODO(meg): Incorporate this from evaluate library.
# import evaluate
from data_measurements.zipf import zipf
from datasets import load_from_disk, load_metric
from nltk.corpus import stopwords
from os import mkdir, getenv
from os.path import exists, isdir
from os.path import join as pjoin
from pathlib import Path
from sklearn.feature_extraction.text import CountVectorizer
from utils.dataset_utils import (CNT, EMBEDDING_FIELD, LENGTH_FIELD,
                                 OUR_TEXT_FIELD, PERPLEXITY_FIELD, PROP,
                                 TEXT_NAN_CNT, TOKENIZED_FIELD, TOT_OPEN_WORDS,
                                 TOT_WORDS, VOCAB, WORD)


pd.options.display.float_format = "{:,.3f}".format

logs = utils.prepare_logging(__file__)

# TODO: Read this in depending on chosen language / expand beyond english
nltk.download("stopwords")
_CLOSED_CLASS = (
        stopwords.words("english")
        + [
            "t",
            "n",
            "ll",
            "d",
            "wasn",
            "weren",
            "won",
            "aren",
            "wouldn",
            "shouldn",
            "didn",
            "don",
            "hasn",
            "ain",
            "couldn",
            "doesn",
            "hadn",
            "haven",
            "isn",
            "mightn",
            "mustn",
            "needn",
            "shan",
            "would",
            "could",
            "dont",
            "u",
        ]
        + [str(i) for i in range(0, 21)]
)
IDENTITY_TERMS = [
    "man",
    "woman",
    "non-binary",
    "gay",
    "lesbian",
    "queer",
    "trans",
    "straight",
    "cis",
    "she",
    "her",
    "hers",
    "he",
    "him",
    "his",
    "they",
    "them",
    "their",
    "theirs",
    "himself",
    "herself",
]
# treating inf values as NaN as well
pd.set_option("use_inf_as_na", True)

MIN_VOCAB_COUNT = 10
_TREE_DEPTH = 12
_TREE_MIN_NODES = 250
# as long as we're using sklearn - already pushing the resources
_MAX_CLUSTER_EXAMPLES = 5000
_NUM_VOCAB_BATCHES = 2000
_TOP_N = 100
_CVEC = CountVectorizer(token_pattern="(?u)\\b\\w+\\b", lowercase=True)

_PERPLEXITY = load_metric("perplexity")


class DatasetStatisticsCacheClass:

    def __init__(
            self,
            cache_dir,
            dset_name,
            dset_config,
            split_name,
            text_field,
            label_field,
            label_names,
            calculation=None,
            use_cache=False,
            save=True,
    ):
        self.label_results = None
        self.duplicates_results = None
        self.calculation = calculation
        self.our_length_field = LENGTH_FIELD
        self.our_tokenized_field = TOKENIZED_FIELD
        self.our_embedding_field = EMBEDDING_FIELD
        self.cache_dir = cache_dir
        # path to the directory used for caching
        if isinstance(text_field, list):
            text_field = "-".join(text_field)
        self.dataset_cache_dir = f"{dset_name}_{dset_config}_{split_name}_{text_field}"
        # TODO: Having "cache_dir" and "cache_path" is confusing.
        self.cache_path = pjoin(
            self.cache_dir,
            self.dataset_cache_dir,
        )
        # Use stored data if there; otherwise calculate afresh
        self.use_cache = use_cache
        # Save newly calculated results.
        self.save = save
        ### What are we analyzing?
        # name of the Hugging Face dataset
        self.dset_name = dset_name
        # name of the dataset config
        self.dset_config = dset_config
        # name of the split to analyze
        self.split_name = split_name
        # TODO: Chould this be "feature" ?
        # which text fields are we analysing?
        self.text_field = text_field
        # which label fields are we analysing?
        self.label_field = label_field
        # what are the names of the classes?
        self.label_names = label_names
        ## Hugging Face dataset objects
        self.dset = None  # original dataset
        # HF dataset with all of the self.text_field instances in self.dset
        self.text_dset = None
        self.dset_peek = None
        # HF dataset with text embeddings in the same order as self.text_dset
        self.embeddings_dset = None
        # HF dataset with all of the self.label_field instances in self.dset
        # TODO: Not being used anymore; make sure & remove.
        self.label_dset = None
        ## Data frames
        # Tokenized text
        self.tokenized_df = None
        # save sentence length histogram in the class so it doesn't ge re-computed
        self.length_df = None
        self.fig_tok_length = None
        # Data Frame version of self.label_dset
        # TODO: Not being used anymore. Make sure and remove
        self.label_df = None
        # save label pie chart in the class so it doesn't ge re-computed
        self.fig_labels = None
        # Save zipf fig so it doesn't need to be recreated.
        self.zipf_fig = None
        # Zipf object
        self.z = None
        # Vocabulary with word counts in the dataset
        self.vocab_counts_df = None
        # Vocabulary filtered to remove stopwords
        self.vocab_counts_filtered_df = None
        self.sorted_top_vocab_df = None
        ## General statistics and duplicates
        self.total_words = 0
        self.total_open_words = 0
        # Number of NaN values (NOT empty strings)
        self.text_nan_count = 0
        # Text Duplicates module
        self.dups_frac = 0
        self.dups_dict = {}
        self.perplexities_df = None
        self.avg_length = None
        self.std_length = None
        self.general_stats_dict = {}
        self.num_uniq_lengths = 0
        # clustering text by embeddings
        # the hierarchical clustering tree is represented as a list of nodes,
        # the first is the root
        self.node_list = []
        # save tree figure in the class so it doesn't ge re-computed
        self.fig_tree = None
        # keep Embeddings object around to explore clusters
        self.embeddings = None
        # nPMI
        # Holds a nPMIStatisticsCacheClass object
        self.npmi_stats = None
        # TODO: Have lowercase be an option for a user to set.
        self.to_lowercase = True
        # The minimum amount of times a word should occur to be included in
        # word-count-based calculations (currently just relevant to nPMI)
        self.min_vocab_count = MIN_VOCAB_COUNT
        self.cvec = _CVEC
        # File definitions
        # path to the directory used for caching
        if not isinstance(text_field, str):
            text_field = ".".join(text_field)
        # if isinstance(label_field, str):
        #    label_field = label_field
        # else:
        #    label_field = "-".join(label_field)
        self.dataset_cache_dir = f"{dset_name}_{dset_config}_{split_name}_{text_field}"
        self.cache_path = pjoin(
            self.cache_dir,
            self.dataset_cache_dir,  # {label_field},
        )
        # Things that get defined later.
        self.fig_tok_length_png = None
        self.length_stats_dict = None

        # Cache files not needed for UI
        self.dset_fid = pjoin(self.cache_path, "base_dset")
        self.tokenized_df_fid = pjoin(self.cache_path, "tokenized_df.feather")
        # TODO: Not being used anymore. Check and remove.
        self.label_dset_fid = pjoin(self.cache_path, "label_dset")

        # Needed for UI -- embeddings
        self.text_dset_fid = pjoin(self.cache_path, "text_dset")
        # Needed for UI
        self.dset_peek_json_fid = pjoin(self.cache_path, "dset_peek.json")

        ## Length cache files
        # Needed for UI
        self.length_df_fid = pjoin(self.cache_path, "length_df.feather")
        # Needed for UI
        self.length_stats_json_fid = pjoin(self.cache_path, "length_stats.json")
        self.vocab_counts_df_fid = pjoin(self.cache_path,
                                         "vocab_counts.feather")
        # Needed for UI
        self.dup_counts_df_fid = pjoin(self.cache_path, "dup_counts_df.feather")
        # Needed for UI
        self.perplexities_df_fid = pjoin(self.cache_path,
                                         "perplexities_df.feather")
        # Needed for UI
        self.fig_tok_length_fid = pjoin(self.cache_path, "fig_tok_length.png")

        ## General text stats
        # Needed for UI
        self.general_stats_json_fid = pjoin(self.cache_path,
                                            "general_stats_dict.json")
        # Needed for UI
        self.sorted_top_vocab_df_fid = pjoin(
            self.cache_path, "sorted_top_vocab.feather"
        )

        self.label_files = {}
        self.duplicates_files = {}

        ## Embeddings cache files
        # Needed for UI
        self.node_list_fid = pjoin(self.cache_path, "node_list.th")
        # Needed for UI
        self.fig_tree_json_fid = pjoin(self.cache_path, "fig_tree.json")
        self.load_or_prepare_dataset()

    def get_base_dataset(self):
        """Gets a pointer to the truncated base dataset object."""
        if not self.dset:
            self.dset = ds_utils.load_truncated_dataset(
                self.dset_name,
                self.dset_config,
                self.split_name,
                cache_name=self.dset_fid,
                use_cache=True,
                use_streaming=True,
            )


    def load_or_prepare_general_stats(self, load_only=False):
        """
        Content for expander_general_stats widget.
        Provides statistics for total words, total open words,
        the sorted top vocab, the NaN count, and the duplicate count.
        Args:

        Returns:

        """
        # General statistics
        # For the general statistics, text duplicates are not saved in their
        # own files, but rather just the text duplicate fraction is saved in the
        # "general" file. We therefore set save=False for
        # the text duplicate filesin this case.
        # Similarly, we don't get the full list of duplicates
        # in general stats, so set list_duplicates to False
        self.load_or_prepare_text_duplicates(load_only=load_only, save=False, list_duplicates=False)
        logs.info(self.duplicates_results)
        self.general_stats_dict.update(self.duplicates_results)
        # TODO: Tighten the rest of this similar to text_duplicates.
        if (
                self.use_cache
                and exists(self.general_stats_json_fid)
                and exists(self.sorted_top_vocab_df_fid)
        ):
            logs.info("Loading cached general stats")
            self.load_general_stats()
        elif not load_only:
            logs.info("Preparing general stats")
            self.prepare_general_stats()
            if self.save:
                ds_utils.write_df(self.sorted_top_vocab_df,
                               self.sorted_top_vocab_df_fid)
                ds_utils.write_json(self.general_stats_dict,
                                 self.general_stats_json_fid)

    def load_or_prepare_text_lengths(self, load_only=False):
        """
        The text length widget relies on this function, which provides
        a figure of the text lengths, some text length statistics, and
        a text length dataframe to peruse.
        Args:
            save:
        Returns:

        """
        # Text length figure
        if self.use_cache and exists(self.fig_tok_length_fid):
            self.fig_tok_length_png = mpimg.imread(self.fig_tok_length_fid)
        elif not load_only:
            self.prepare_fig_text_lengths()
            if self.save:
                self.fig_tok_length.savefig(self.fig_tok_length_fid)
        # Text length dataframe
        if self.use_cache and exists(self.length_df_fid):
            self.length_df = ds_utils.read_df(self.length_df_fid)
        elif not load_only:
            self.prepare_length_df()
            if self.save:
                ds_utils.write_df(self.length_df, self.length_df_fid)

        # Text length stats.
        if self.use_cache and exists(self.length_stats_json_fid):
            with open(self.length_stats_json_fid, "r") as f:
                self.length_stats_dict = json.load(f)
            self.avg_length = self.length_stats_dict["avg length"]
            self.std_length = self.length_stats_dict["std length"]
            self.num_uniq_lengths = self.length_stats_dict["num lengths"]
        elif not load_only:
            self.prepare_text_length_stats()
            if self.save:
                ds_utils.write_json(self.length_stats_dict,
                                 self.length_stats_json_fid)

    def prepare_length_df(self):
        self.tokenized_df[LENGTH_FIELD] = self.tokenized_df[
            TOKENIZED_FIELD].apply(
            len
        )
        self.length_df = self.tokenized_df[
            [LENGTH_FIELD, OUR_TEXT_FIELD]
        ].sort_values(by=[LENGTH_FIELD], ascending=True)

    def prepare_text_length_stats(self):
        if (
                LENGTH_FIELD not in self.tokenized_df.columns
                or self.length_df is None
        ):
            self.prepare_length_df()
        avg_length = sum(self.tokenized_df[LENGTH_FIELD]) / len(
            self.tokenized_df[LENGTH_FIELD]
        )
        self.avg_length = round(avg_length, 1)
        std_length = statistics.stdev(self.tokenized_df[LENGTH_FIELD])
        self.std_length = round(std_length, 1)
        self.num_uniq_lengths = len(self.length_df["length"].unique())
        self.length_stats_dict = {
            "avg length": self.avg_length,
            "std length": self.std_length,
            "num lengths": self.num_uniq_lengths,
        }

    def prepare_fig_text_lengths(self):
        if LENGTH_FIELD not in self.tokenized_df.columns:
            self.prepare_length_df()
        self.fig_tok_length = make_fig_lengths(self.tokenized_df,
                                               LENGTH_FIELD)

    def load_or_prepare_embeddings(self, load_only=False):
        # TODO: Incorporate 'load only' if we use this widget.
        """Uses an Embeddings class specific to this project,
           which uses the attributes defined in this file directly"""
        self.embeddings = Embeddings(self, use_cache=self.use_cache)
        self.embeddings.make_hierarchical_clustering()
        self.node_list = self.embeddings.node_list
        self.fig_tree = self.embeddings.fig_tree

    ## Labels functions
    def load_or_prepare_labels(self, load_only=False):
        """Uses a generic Labels class, with attributes specific to this
        project as input.
        Computes results for each label column,
        or else uses what's available in the cache.
        Currently supports Datasets with just one label column.
        """
        label_obj = labels.DMTHelper(self, load_only=load_only, save=self.save)
        label_obj.run_DMT_processing()
        self.fig_labels = label_obj.fig_labels
        self.label_results = label_obj.label_results
        self.label_files = label_obj.get_label_filenames()

    # Get vocab with word counts
    def load_or_prepare_vocab(self, load_only=False):
        """
        Calculates the vocabulary count from the tokenized text.
        The resulting dataframes may be used in nPMI calculations, zipf, etc.
        :param
        :return:
        """
        if self.use_cache and exists(self.vocab_counts_df_fid):
            logs.info("Reading vocab from cache")
            self.load_vocab()
            self.vocab_counts_filtered_df = filter_vocab(self.vocab_counts_df)
        elif not load_only:
            # Building the vocabulary starts with tokenizing.
            self.load_or_prepare_tokenized_df(load_only=False)
            logs.info("Calculating vocab afresh")
            word_count_df = count_vocab_frequencies(self.tokenized_df)
            logs.info("Making dfs with proportion.")
            self.vocab_counts_df = calc_p_word(word_count_df)
            self.vocab_counts_filtered_df = filter_vocab(self.vocab_counts_df)
            if self.save:
                logs.info("Writing out.")
                ds_utils.write_df(self.vocab_counts_df, self.vocab_counts_df_fid)
        logs.info("unfiltered vocab")
        logs.info(self.vocab_counts_df)
        logs.info("filtered vocab")
        logs.info(self.vocab_counts_filtered_df)

    def load_vocab(self):
        with open(self.vocab_counts_df_fid, "rb") as f:
            self.vocab_counts_df = ds_utils.read_df(f)
        # Handling for changes in how the index is saved.
        self.vocab_counts_df = _set_idx_col_names(self.vocab_counts_df)

    def load_or_prepare_text_duplicates(self, load_only=False, save=True, list_duplicates=True):
        """Uses a text duplicates library, which
        returns strings with their counts, fraction of data that is duplicated,
        or else uses what's available in the cache.
        """
        dups_obj = td.DMTHelper(self, load_only=load_only, save=save)
        dups_obj.run_DMT_processing(list_duplicates=list_duplicates)
        self.duplicates_results = dups_obj.duplicates_results
        self.dups_frac = self.duplicates_results[td.DUPS_FRAC]
        if list_duplicates and td.DUPS_DICT in self.duplicates_results:
            self.dups_dict = self.duplicates_results[td.DUPS_DICT]
        self.duplicates_files = dups_obj.get_duplicates_filenames()


    def load_or_prepare_text_perplexities(self, load_only=False):
        if self.use_cache and exists(self.perplexities_df_fid):
            with open(self.perplexities_df_fid, "rb") as f:
                self.perplexities_df = ds_utils.read_df(f)
        elif not load_only:
            self.prepare_text_perplexities()
            if self.save:
                ds_utils.write_df(self.perplexities_df,
                               self.perplexities_df_fid)

    def load_general_stats(self):
        self.general_stats_dict = json.load(
            open(self.general_stats_json_fid, encoding="utf-8")
        )
        with open(self.sorted_top_vocab_df_fid, "rb") as f:
            self.sorted_top_vocab_df = ds_utils.read_df(f)
        self.text_nan_count = self.general_stats_dict[TEXT_NAN_CNT]
        self.dups_frac = self.general_stats_dict[td.DUPS_FRAC]
        self.total_words = self.general_stats_dict[TOT_WORDS]
        self.total_open_words = self.general_stats_dict[TOT_OPEN_WORDS]

    def prepare_general_stats(self):
        if self.tokenized_df is None:
            logs.warning("Tokenized dataset not yet loaded; doing so.")
            self.load_or_prepare_tokenized_df()
        if self.vocab_counts_df is None:
            logs.warning("Vocab not yet loaded; doing so.")
            self.load_or_prepare_vocab()
        self.sorted_top_vocab_df = self.vocab_counts_filtered_df.sort_values(
            "count", ascending=False
        ).head(_TOP_N)
        self.total_words = len(self.vocab_counts_df)
        self.total_open_words = len(self.vocab_counts_filtered_df)
        self.text_nan_count = int(self.tokenized_df.isnull().sum().sum())
        self.general_stats_dict = {
            TOT_WORDS: self.total_words,
            TOT_OPEN_WORDS: self.total_open_words,
            TEXT_NAN_CNT: self.text_nan_count,
            td.DUPS_FRAC: self.dups_frac
        }

    def prepare_text_perplexities(self):
        if self.text_dset is None:
            self.load_or_prepare_text_dset()
        results = _PERPLEXITY.compute(
            input_texts=self.text_dset[OUR_TEXT_FIELD], model_id='gpt2')
        perplexities = {PERPLEXITY_FIELD: results["perplexities"],
                        OUR_TEXT_FIELD: self.text_dset[OUR_TEXT_FIELD]}
        self.perplexities_df = pd.DataFrame(perplexities).sort_values(
            by=PERPLEXITY_FIELD, ascending=False)

    def load_or_prepare_dataset(self, load_only=False):
        """
        Prepares the HF datasets and data frames containing the untokenized and
        tokenized text as well as the label values.
        self.tokenized_df is used further for calculating text lengths,
        word counts, etc.
        Args:
            save: Store the calculated data to disk.

        Returns:

        """
        print("hi?")
        logs.info("Doing text dset.")
        self.load_or_prepare_text_dset(load_only=load_only)

    def load_or_prepare_dset_peek(self, load_only=False):
        if self.use_cache and exists(self.dset_peek_json_fid):
            with open(self.dset_peek_json_fid, "r") as f:
                self.dset_peek = json.load(f)["dset peek"]
        elif not load_only:
            if self.dset is None:
                self.get_base_dataset()
            self.dset_peek = self.dset[:100]
            if self.save:
                ds_utils.write_json({"dset peek": self.dset_peek},
                                 self.dset_peek_json_fid)

    def load_or_prepare_tokenized_df(self, load_only=False):
        if self.use_cache and exists(self.tokenized_df_fid):
            self.tokenized_df = ds_utils.read_df(self.tokenized_df_fid)
        elif not load_only:
            # tokenize all text instances
            self.tokenized_df = self.do_tokenization()
            if self.save:
                logs.warning("Saving tokenized dataset to disk")
                # save tokenized text
                ds_utils.write_df(self.tokenized_df, self.tokenized_df_fid)

    def load_or_prepare_text_dset(self, load_only=False):
        if self.use_cache and exists(self.text_dset_fid):
            # load extracted text
            self.text_dset = load_from_disk(self.text_dset_fid)
            logs.warning("Loaded dataset from disk")
            logs.warning(self.text_dset)
        # ...Or load it from the server and store it anew
        elif not load_only:
            self.prepare_text_dset()
            if self.save:
                # save extracted text instances
                logs.warning("Saving dataset to disk")
                self.text_dset.save_to_disk(self.text_dset_fid)

    def prepare_text_dset(self):
        self.get_base_dataset()
        logs.warning(self.dset)
        # extract all text instances
        self.text_dset = self.dset.map(
            lambda examples: ds_utils.extract_field(
                examples, self.text_field, OUR_TEXT_FIELD
            ),
            batched=True,
            remove_columns=list(self.dset.features),
        )

    def do_tokenization(self):
        """
        Tokenizes the dataset
        :return:
        """
        if self.text_dset is None:
            self.load_or_prepare_text_dset()
        sent_tokenizer = self.cvec.build_tokenizer()

        def tokenize_batch(examples):
            # TODO: lowercase should be an option
            res = {
                TOKENIZED_FIELD: [
                    tuple(sent_tokenizer(text.lower()))
                    for text in examples[OUR_TEXT_FIELD]
                ]
            }
            res[LENGTH_FIELD] = [len(tok_text) for tok_text in
                                 res[TOKENIZED_FIELD]]
            return res

        tokenized_dset = self.text_dset.map(
            tokenize_batch,
            batched=True,
            # remove_columns=[OUR_TEXT_FIELD], keep around to print
        )
        tokenized_df = pd.DataFrame(tokenized_dset)
        return tokenized_df

    def load_or_prepare_npmi(self, load_only=False):
        npmi_obj = npmi.DMTHelper(self, IDENTITY_TERMS, use_cache=self.use_cache, save=self.save)
        npmi_obj.run_DMT_processing(load_only=load_only)
        self.npmi_results = npmi_obj.npmi_results
        self.npmi_files = npmi_obj.get_filenames()


    def load_or_prepare_zipf(self, save=True):
        if self.use_cache:
            zipf_json_fid, zipf_fig_json_fid, zipf_fig_html_fid = zipf.get_zipf_fids(
                self.cache_path)
        if self.use_cache and exists(zipf_json_fid):
            # Zipf statistics
            # Read Zipf statistics: Alpha, p-value, etc.
            with open(zipf_json_fid, "r") as f:
                zipf_dict = json.load(f)
            self.z = zipf.Zipf(self.vocab_counts_df)
            self.z.load(zipf_dict)
            # Zipf figure
            if exists(zipf_fig_json_fid):
                self.zipf_fig = ds_utils.read_plotly(zipf_fig_json_fid)
            elif not load_only:
                self.zipf_fig = zipf.make_zipf_fig(self.z)
                if self.save:
                    ds_utils.write_plotly(self.zipf_fig)
        elif not load_only:
            self.prepare_zipf()
            if self.save:
                zipf_dict = self.z.get_zipf_dict()
                ds_utils.write_json(zipf_dict, zipf_json_fid)
                ds_utils.write_plotly(self.zipf_fig, zipf_fig_json_fid)
                self.zipf_fig.write_html(zipf_fig_html_fid)

    def prepare_zipf(self):
        # Calculate zipf from scratch
        # TODO: Does z even need to be self?
        self.z = zipf.Zipf(self.vocab_counts_df)
        self.z.calc_fit()
        self.zipf_fig = zipf.make_zipf_fig(self.z)

def _set_idx_col_names(input_vocab_df):
    if input_vocab_df.index.name != VOCAB and VOCAB in input_vocab_df.columns:
        input_vocab_df = input_vocab_df.set_index([VOCAB])
        input_vocab_df[VOCAB] = input_vocab_df.index
    return input_vocab_df


def dummy(doc):
    return doc


def count_vocab_frequencies(tokenized_df):
    """
    Based on an input pandas DataFrame with a 'text' column,
    this function will count the occurrences of all words.
    :return: [num_words x num_sentences] DataFrame with the rows corresponding to the
    different vocabulary words and the column to the presence (0 or 1) of that word.
    """

    cvec = CountVectorizer(
        tokenizer=dummy,
        preprocessor=dummy,
    )
    # We do this to calculate per-word statistics
    # Fast calculation of single word counts
    logs.info(
        "Fitting dummy tokenization to make matrix using the previous tokenization"
    )
    cvec.fit(tokenized_df[TOKENIZED_FIELD])
    document_matrix = cvec.transform(tokenized_df[TOKENIZED_FIELD])
    batches = np.linspace(0, tokenized_df.shape[0], _NUM_VOCAB_BATCHES).astype(
        int)
    i = 0
    tf = []
    while i < len(batches) - 1:
        logs.info("%s of %s vocab batches" % (str(i), str(len(batches))))
        batch_result = np.sum(
            document_matrix[batches[i]: batches[i + 1]].toarray(), axis=0
        )
        tf.append(batch_result)
        i += 1
    word_count_df = pd.DataFrame(
        [np.sum(tf, axis=0)], columns=cvec.get_feature_names()
    ).transpose()
    # Now organize everything into the dataframes
    word_count_df.columns = [CNT]
    word_count_df.index.name = WORD
    return word_count_df


def calc_p_word(word_count_df):
    # p(word)
    word_count_df[PROP] = word_count_df[CNT] / float(sum(word_count_df[CNT]))
    vocab_counts_df = pd.DataFrame(
        word_count_df.sort_values(by=CNT, ascending=False))
    vocab_counts_df[VOCAB] = vocab_counts_df.index
    return vocab_counts_df


def filter_vocab(vocab_counts_df):
    # TODO: Add warnings (which words are missing) to log file?
    filtered_vocab_counts_df = vocab_counts_df.drop(_CLOSED_CLASS,
                                                    errors="ignore")
    filtered_count = filtered_vocab_counts_df[CNT]
    filtered_count_denom = float(sum(filtered_vocab_counts_df[CNT]))
    filtered_vocab_counts_df[PROP] = filtered_count / filtered_count_denom
    return filtered_vocab_counts_df


## Figures ##

def make_fig_lengths(tokenized_df, length_field):
    fig_tok_length, axs = plt.subplots(figsize=(15, 6), dpi=150)
    sns.histplot(data=tokenized_df[length_field], kde=True, bins=100, ax=axs)
    sns.rugplot(data=tokenized_df[length_field], ax=axs)
    return fig_tok_length





## Input/Output ##


def intersect_dfs(df_dict):
    started = 0
    new_df = None
    for key, df in df_dict.items():
        if df is None:
            continue
        for key2, df2 in df_dict.items():
            if df2 is None:
                continue
            if key == key2:
                continue
            if started:
                new_df = new_df.join(df2, how="inner", lsuffix="1", rsuffix="2")
            else:
                new_df = df.join(df2, how="inner", lsuffix="1", rsuffix="2")
                started = 1
    return new_df.copy()


