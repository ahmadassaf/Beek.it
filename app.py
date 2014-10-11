# coding: utf-8

"""
A microservice, that takes an URL and saves the url in some database.
"""

from flask import Flask, request, abort, jsonify, g, render_template, redirect, url_for
from jobs import *
from redis import Redis
from rq import Queue
from utils import url_to_doc_id, pretty_date
import datetime
import dateutil.parser
import elasticsearch
import time

app = Flask(__name__)

@app.template_filter('human_category')
def human_category(s):
    """ 2014-10-11T08:53:18.392370 """
    cats = {
        'culture_politics': 'Culture/Politics',
        'recreation': 'Recreation', 
        'computer_internet': 'Computer/Internet',
        'science_technology': 'Science/Technology',
        'arts_entertainment': 'Arts/Entertainment',
        'business': 'Business',
    }
    return cats.get(s, s)


@app.template_filter('human_time')
def human_time(s):
    """ 2014-10-11T08:53:18.392370 """
    return pretty_date(dateutil.parser.parse(s))


@app.route('/hello')
def root():
    return app.send_static_file('index.html')

@app.route("/bookmarked")
def bookmarked():
    import urllib
    url = request.args.get('url')
    query = {'query_string': {'query': '%s' % url}}
    es = elasticsearch.Elasticsearch()
    result = es.search(index='beek', body={
            "query" : query}, size=100)
    return jsonify({'bookmarked': result['hits']['total']!=0})


@app.route("/terms")
def terms():
    es = elasticsearch.Elasticsearch()
    result = es.search(index='beek', body={
            "query" : { "match_all" : {}}}, size=100)
    data = result['hits']['hits']
    cities = filter_type_from_results('City', data)
    people = filter_type_from_results('Person', data)

    cats = set()
    for row in data:
        cat = row['_source'].get('category', None)
        if cat: 
            cats.add(cat)

    return jsonify({'cities':cities, 'people':people, 'categories':list(cats)})


def filter_type_from_results(ent_type, results):
    terms = dict()
    for result in results:
        print result['_source'].keys()
        for entity in result['_source'].get('entities',[]):
            
            if entity['type'] == ent_type and entity.get('disambiguated'):
                    disam = entity['disambiguated']
                    terms[ disam['name'] ] = disam.get('dbpedia', '')
    return terms

@app.route("/")
def home():
    q = request.args.get('q')
    # add new URL
    if q and q.strip().startswith('+'):
        url = q.strip().strip('+ ')
        # append http:// if needed
        if not url.startswith('http'):
            url = 'http://%s' % url
        return redirect(url_for('add_url', url=url))
    if not q:
        # get some stats
        es = elasticsearch.Elasticsearch()
        total = es.count(index='beek', body={'query': {'match_all': {}}}).get('count')
        result = es.search(index='beek', body={
            "query" : { "match_all" : {}}}, size=5)

        return render_template('home.html', docs=result['hits'], total=total)

    es = elasticsearch.Elasticsearch()
    query = {'query_string': {'query': '%s' % q}}
    result = es.search(index='beek', doc_type='page', body={
        'query': query,
        'highlight': {'fields': {'text':
            {"fragment_size" : 90, "number_of_fragments" : 1}}}})

    people = filter_type_from_results('Person', result['hits']['hits'])

    return render_template('home.html', hits=result['hits'], people=people)    
    # return "<pre>%s</pre>" % (hits)



@app.route("/api/remove")
def remove_url():
    if request.args.get('id'):
        es = elasticsearch.Elasticsearch()
        es.delete(index='beek', doc_type='page', id=request.args.get('id'))
    return redirect(url_for('home'))


@app.route("/api/add")
def add_url():
    url = request.args.get('url')
    if not url:
        return jsonify(msg='no url supplied'), 400

    q = Queue(connection=Redis())
    # First, index the page
    index_job = q.enqueue(index, url)
    # In parallel, do Alchemy on URL and store it in a separate doc type
    alchemy_job = q.enqueue(query_alchemy, url, depends_on=index_job)
    # Embedly ...
    embedly_job = q.enqueue(query_embedly, url, depends_on=alchemy_job)
    # Count the words in the page ...
    wordcount_job = q.enqueue(count_words, url_to_doc_id(url), depends_on=embedly_job)

    # return jsonify(msg="ok enqueued")
    return redirect(url_for('home'))



if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)
