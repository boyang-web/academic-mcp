from academic_data_mcp.gateways.whu.catalog import catalogue_rows, database_detail, purchased_mentions


def test_complete_catalogue_separates_subscription_candidates_from_trials():
    payload=[{'tagSet':[
        {'customTagName':'中文库','portalGuideVoList':[{'databaseNum':'DB_A','customName':'A'}]},
        {'customTagName':'试用库','portalGuideVoList':[{'databaseNum':'DB_B','customName':'B'}]},
        {'customTagName':'unexpected untrusted tag','portalGuideVoList':[{'databaseNum':'DB_C','customName':'C'}]}]}]
    rows=catalogue_rows(payload,'https://whu.metaersp.cn/databaseList')
    assert [r['id'] for r in rows]==['DB_A','DB_B']
    assert rows[0]['subscription_candidate'] and not rows[1]['subscription_candidate']
    assert all(r['subscription_status']=='未核实' and not r['access_verified'] for r in rows)


def test_supplier_product_count_is_not_a_purchased_scope():
    assert purchased_mentions('<p>产品涵盖200多个数据库；1、股票；2、财务。</p>')==[]
    assert purchased_mentions('<p>我校已采购的数据库内容包括：1、股票市场系列；2、公司研究系列。</p>')==['股票市场系列','公司研究系列']


def test_numbered_cnki_purchases_keep_names_separate_from_description():
    intro='<p>下列中国知网的核心子库我馆已购买</p><p>1. 期刊库：我校购买1994年后。</p><p>2. 学位论文：1999年后。</p>'
    assert purchased_mentions(intro)==['期刊库','学位论文']


def test_foreign_child_relations_never_prove_whu_entitlement():
    row={'id':'DB_A','name':'A'}
    data={'libraryCode':'131001','customName':'A','otherTagName':'订阅'}
    result=database_detail(data,row,[{'name':'foreign child','source_library_code':'287017'}],1,True)
    assert result['subscription_verified']
    assert result['structured_children_complete']
    assert not result['child_scope_verified'] and not result['access_verified']


def test_empty_child_api_does_not_erase_explicit_purchase_mentions():
    result=database_detail({'libraryCode':'131001','otherTagName':'订阅',
        'intro':'我校已采购的数据库内容包括：1、股票市场系列；2、公司研究系列。'},
        {'id':'DB_A','name':'A'},[],0,True)
    assert len(result['purchased_scope_mentions'])==2
    assert result['structured_children_total']==0
    assert not result['child_scope_verified']
